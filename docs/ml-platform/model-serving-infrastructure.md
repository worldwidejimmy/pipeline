# LLM Model Serving Infrastructure Guide

**Owner:** ML Platform Team  
**Last Updated:** 2025-10-08  
**Applies To:** All teams deploying LLM inference workloads

---

## Overview

This guide covers how to deploy, configure, and operate LLM serving infrastructure at Meridian Data Corp. We use a tiered serving strategy: managed APIs for most workloads, self-hosted vLLM for high-volume or data-sensitivity-constrained use cases.

---

## Serving Tiers

### Tier 1 — Managed API (Default)

Use managed APIs for most use cases. Prefer this unless:
- Data cannot leave Meridian's infrastructure (PII, MNPI, trade secrets)
- Volume exceeds $15K/month in managed API costs
- Latency SLA cannot be met with managed API p99

**Supported providers:**
- **Anthropic** (Claude family) — primary for agentic and reasoning workloads
- **OpenAI** (GPT-4o family) — secondary, particularly for function calling
- **Groq** — for latency-sensitive workloads (language models with < 50ms TTFT)
- **Google Vertex AI** (Gemini family) — available for teams with GCP budget

### Tier 2 — Self-Hosted vLLM

For workloads requiring on-premise inference or cost optimization at scale:

**Models supported on internal GPU cluster:**
- `meta-llama/Llama-3.1-70B-Instruct` — general use, strong instruction following
- `Qwen/Qwen2.5-72B-Instruct` — strong at code and technical reasoning
- `mistralai/Mistral-7B-Instruct-v0.3` — fast, low-cost for simple tasks
- `deepseek-ai/DeepSeek-R1-Distill-Qwen-7B` — reasoning tasks (chain-of-thought)

**GPU cluster:** 8x A100 80GB nodes (4 reserved for training, 4 for inference).

### Tier 3 — Local Inference (Development Only)

Use Ollama for local development and testing. Do not use local inference in production.

```bash
ollama pull llama3.1:8b
ollama pull nomic-embed-text
ollama serve
```

---

## vLLM Deployment

### Key Concepts

**PagedAttention:** vLLM's core innovation. KV cache is managed in fixed-size pages (like OS virtual memory), eliminating memory fragmentation and enabling efficient sharing of KV cache across parallel requests. This is why vLLM achieves 2–4x higher throughput than naive HuggingFace inference at the same hardware budget.

**Continuous Batching:** vLLM dynamically batches incoming requests at the iteration level, not the request level. New requests are added to in-flight batches as soon as a sequence finishes, maximizing GPU utilization. Naive static batching wastes GPU cycles waiting for the slowest sequence.

**Quantization Options:**
- `AWQ` (Activation-aware Weight Quantization) — recommended for 4-bit. Best quality/speed tradeoff.
- `GPTQ` — widely supported, slightly lower quality than AWQ.
- `GGUF` — used by llama.cpp/Ollama. Not supported by vLLM natively.
- `FP8` — native on H100 GPUs. Best throughput on H100 hardware.

### Kubernetes Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-llama3-70b
  namespace: ml-inference
spec:
  replicas: 2
  selector:
    matchLabels:
      app: vllm-llama3-70b
  template:
    spec:
      containers:
      - name: vllm
        image: vllm/vllm-openai:v0.6.3
        args:
          - --model=meta-llama/Llama-3.1-70B-Instruct
          - --tensor-parallel-size=4     # Split across 4 GPUs
          - --max-model-len=32768
          - --quantization=awq
          - --gpu-memory-utilization=0.90
          - --max-num-seqs=256           # Max concurrent sequences
          - --served-model-name=llama3-70b
        resources:
          limits:
            nvidia.com/gpu: "4"
            memory: "160Gi"
        ports:
          - containerPort: 8000
      nodeSelector:
        accelerator: a100-80gb
      tolerations:
        - key: "nvidia.com/gpu"
          operator: "Exists"
          effect: "NoSchedule"
```

### vLLM Performance Tuning

| Parameter | Effect | Recommendation |
|---|---|---|
| `--gpu-memory-utilization` | Fraction of GPU VRAM for KV cache | 0.85–0.92 (leave headroom for activations) |
| `--max-num-seqs` | Max concurrent request sequences | Start at 256, increase if GPU utilization < 80% |
| `--tensor-parallel-size` | Number of GPUs to split model across | Must be multiple of attention heads; use 4 for 70B models |
| `--max-model-len` | Maximum context length (prompt + output) | Set to model max or reduce to save KV cache memory |
| `--block-size` | KV cache page size in tokens | 16 (default) — increase to 32 for long-context workloads |

---

## Fine-Tuning Overview

### When to Fine-Tune vs RAG

| Need | Prefer |
|---|---|
| Up-to-date knowledge | RAG |
| Proprietary documents | RAG |
| Style / format / persona | Fine-tuning |
| Domain-specific reasoning patterns | Fine-tuning |
| Specialized output structure | Fine-tuning |
| Latency-sensitive (cache embeddings) | RAG with semantic cache |

**Rule:** Exhaust RAG, prompt engineering, and few-shot examples before committing to fine-tuning. Fine-tuning a 70B model costs $800–3,000 per run and takes engineering bandwidth to maintain.

### LoRA and QLoRA

**LoRA (Low-Rank Adaptation):** Adds small trainable matrices (rank r=8 or 16) to attention layers. Base model weights are frozen. The adapter is ~0.1–1% of the original model size. Fast to train, easy to swap at inference time.

**QLoRA:** LoRA + 4-bit quantization of the base model. Enables fine-tuning a 70B model on 2x A100 80GB GPUs instead of requiring 8. Small quality degradation vs full LoRA; acceptable for most use cases.

**Our standard fine-tuning stack:**
- **Library:** HuggingFace `peft` + `trl` (`SFTTrainer`)
- **Quantization:** `bitsandbytes` for QLoRA
- **Experiment tracking:** MLflow (runs stored in `mlflow.internal`)
- **Model registry:** MLflow Model Registry

```python
from peft import LoraConfig, get_peft_model
from trl import SFTTrainer

lora_config = LoraConfig(
    r=16,                          # Rank
    lora_alpha=32,                 # Scaling factor
    target_modules=["q_proj", "v_proj"],  # Attention matrices
    lora_dropout=0.05,
    bias="none",
    task_type="CAUSAL_LM",
)
```

### DPO (Direct Preference Optimization)

DPO is an alternative to RLHF that trains on preference pairs (chosen vs rejected responses) without a separate reward model. Use DPO when:
- You have human preference labels or can generate them with a strong LLM judge
- RLHF instability is a concern
- You want to align model responses to company style or safety guidelines

**DPO is significantly simpler to implement than RLHF** — no reward model training, no PPO. Recommended for teams new to fine-tuning alignment.

---

## Speculative Decoding

For latency-sensitive applications, speculative decoding uses a small "draft" model to propose tokens that a larger "target" model then verifies in parallel. On average, 2–3 tokens are accepted per verification step, giving 2–3x throughput improvement with no quality degradation.

**When to use:**
- Output latency SLA < 150ms TTFT
- Batch size is small (< 8 concurrent requests)
- Draft and target models share the same tokenizer

**vLLM support:** `--speculative-model=<draft-model>` + `--num-speculative-tokens=5`

**Recommended pairs:**
- Target: `Llama-3.1-70B-Instruct`, Draft: `Llama-3.2-1B-Instruct`
- Target: `Qwen2.5-72B-Instruct`, Draft: `Qwen2.5-0.5B-Instruct`

---

## Cost Reference

### Managed API (per 1M tokens, as of Q4 2025)

| Model | Input | Output |
|---|---|---|
| claude-haiku-3-5 | $1.00 | $5.00 |
| claude-sonnet-4-5 | $3.00 | $15.00 |
| claude-opus-4 | $15.00 | $75.00 |
| gpt-4o-mini | $0.15 | $0.60 |
| gpt-4o | $2.50 | $10.00 |
| Groq Llama-3.1-70B | $0.59 | $0.79 |

### Self-Hosted (vLLM on A100 80GB)

| Config | Throughput | Cost/Hour | Cost per 1M output tokens |
|---|---|---|---|
| 1x A100, Llama-3.1-8B (FP16) | ~2,800 tok/s | $3.50 | ~$0.35 |
| 4x A100, Llama-3.1-70B (AWQ) | ~950 tok/s | $14.00 | ~$4.10 |
| 8x A100, Llama-3.1-70B (FP16) | ~1,800 tok/s | $28.00 | ~$4.32 |

Self-hosted becomes cost-effective vs managed APIs at roughly > 5M tokens/day sustained throughput.

---

## Monitoring

### Required Dashboards

All vLLM deployments must have Grafana dashboards covering:

- **TTFT (Time to First Token)** — p50, p95, p99 by model
- **Token throughput** — tokens/second generated
- **KV cache utilization** — percentage of cache blocks in use
- **Queue depth** — pending requests waiting for a sequence slot
- **GPU utilization and memory** — from DCGM exporter
- **Request success rate** — 2xx vs 4xx vs 5xx

vLLM natively exposes Prometheus metrics at `:8000/metrics`. Scrape with:

```yaml
# prometheus.yaml scrape config
- job_name: 'vllm'
  static_configs:
    - targets: ['vllm-service.ml-inference:8000']
```

### Alerting Thresholds

| Metric | Warning | Critical |
|---|---|---|
| TTFT p95 | > 3s | > 8s |
| KV cache utilization | > 85% | > 95% |
| Queue depth | > 50 requests | > 200 requests |
| GPU memory | > 90% | > 98% |
| Request error rate | > 1% | > 5% |
