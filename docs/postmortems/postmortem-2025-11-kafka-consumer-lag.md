# Postmortem: Kafka Consumer Lag — ML Inference Pipeline

**Incident ID:** INC-2025-1107  
**Severity:** SEV-2  
**Date:** 2025-11-07  
**Duration:** 3 hours 42 minutes (14:17 – 18:01 UTC)  
**Incident Commander:** Priya Nair  
**Authors:** Priya Nair, Dev Patel  
**Status:** Complete — all action items tracked in Jira (PLAT-4821)

---

## Summary

A Kafka consumer group serving the real-time ML inference pipeline experienced sustained lag growth on the `ml.inference.requests` topic, causing recommendation latency to degrade from a p99 of 180ms to over 8 seconds for a period of 3 hours and 42 minutes. Approximately 14% of users received degraded recommendations during the incident window. No data loss occurred.

Root cause: A memory leak in the feature enrichment service (v2.14.3) caused gradual OOM kills of consumer pods, reducing effective consumer parallelism from 24 to 3 over a period of ~90 minutes before alerting fired.

---

## Timeline (all times UTC)

| Time | Event |
|---|---|
| 12:45 | Feature enrichment service v2.14.3 deployed to production via standard canary rollout (5% → 25% → 100% over 45 minutes). Deployment completed without errors. |
| 13:30 | Memory usage of enrichment pods begins slow upward trend. Not yet alerting (threshold: 90% of request limit). |
| 14:05 | First consumer pod OOM-killed by kubelet. Kubernetes restarts pod — consumer group rebalances. Lag begins accumulating at ~2,000 messages/minute. |
| 14:17 | Kafka consumer lag alert fires: `ml-inference-consumer` group lag > 500K messages. On-call paged. |
| 14:28 | On-call engineer Priya Nair acknowledges. Incident channel `#inc-2025-1107` created. |
| 14:35 | Initial assessment: consumer pods cycling. Memory limit 512Mi is being hit repeatedly. Hypothesis: memory leak in new deployment. |
| 14:52 | Rollback to v2.14.2 initiated: `kubectl rollout undo deployment/feature-enrichment -n production`. |
| 15:10 | Rollback complete. Memory stabilizes. OOM kills stop. Consumer group rebalancing completes. |
| 15:30 | Consumer lag still growing — 3 consumers are now recovering a backlog of 4.2M messages with default throughput. |
| 15:45 | Decision: scale consumer group from 8 to 24 replicas to burn down backlog faster. `kubectl scale deployment/ml-inference-consumer --replicas=24 -n production`. |
| 16:20 | Consumer lag peaks at 6.8M messages, then begins declining as scaled consumers burn down backlog. |
| 17:40 | Consumer lag < 50K messages. Recommendation latency returning to baseline. |
| 18:01 | Consumer lag < 5K (within normal operating range). Incident declared resolved. Scaled consumers reduced back to 8 replicas. |

---

## Root Cause Analysis

### Primary Root Cause
The feature enrichment service v2.14.3 introduced a change to cache feature vectors in-process (PR #4412, "Perf: cache enriched feature vectors for dedup reduction"). The cache used an unbounded `dict` with no TTL or eviction policy. Under production traffic (~12K requests/second), this cache grew without bound.

Memory growth rate: approximately 45MB per minute under production load.

Pod memory limit was 512Mi. At this growth rate, pods reached the limit in approximately 11 minutes after stabilizing post-restart, triggering a continuous OOM-kill/restart loop.

### Contributing Factors

1. **Canary rollout did not catch the leak.** At 5% and 25% canary traffic (~600–3,000 req/s), memory growth was slow enough (< 10MB/min) that it did not breach the alert threshold (90% of limit) during the 45-minute rollout window.

2. **No memory growth rate alert.** Alerting was configured only for absolute memory threshold (90% of limit). A rate-of-change alert on memory (`increase(container_memory_working_set_bytes[10m])`) would have caught the trend earlier.

3. **Consumer lag alert threshold too high.** The lag alert threshold was 500K messages, which represents ~7 minutes of backlog at normal throughput. By the time the alert fired, 3 of 8 consumers had already been OOM-killed.

4. **No circuit breaker between consumer lag and recommendation serving.** The recommendation service continued serving inference requests even as consumer lag degraded data freshness. Users received recommendations based on feature data that was 30–90 minutes stale with no indication.

5. **Load testing gaps.** The enrichment service performance tests ran for 5 minutes. The memory leak required ~11 minutes of sustained load to manifest. Tests did not catch it.

---

## Impact

- **Duration:** 3 hours 42 minutes of degraded recommendation quality
- **User impact:** ~14% of active users (based on recommendation API error rate + latency SLO breach)
- **SLO breach:** Recommendation latency SLO (p99 < 500ms) breached for 3 hours 18 minutes
- **Data impact:** None — no messages lost. Kafka retained all messages within retention window.
- **Revenue impact:** Minor. Customer Success estimated < 0.2% session abandonment increase.

---

## What Went Well

- Rollback was clean and completed in 18 minutes from decision to stable.
- On-call engineer correctly identified the memory leak hypothesis within 20 minutes.
- Scaling consumers to burn down the backlog was the right call and worked effectively.
- Communication to `#incidents` and stakeholders was timely and clear.
- Kafka's retention policy meant no messages were lost despite the 3+ hour processing delay.

---

## What Went Poorly

- 90 minutes elapsed between deployment and alert firing — far too long for a production incident.
- The canary process gave false confidence. The rollout window (45 min) was shorter than the failure manifestation time (~11 min at full traffic).
- No automated memory growth rate alerting.
- Consumer lag alert threshold was calibrated for brief, transient lag, not sustained consumer failure.
- Engineers had no visibility into recommendation data staleness — no metric exposed feature freshness age.

---

## Action Items

| Item | Owner | Priority | Due Date | Jira |
|---|---|---|---|---|
| Add memory growth rate alert (`increase(...[10m]) > 50MB`) to all consumer services | Dev Patel | P1 | 2025-11-14 | PLAT-4822 |
| Lower Kafka consumer lag alert threshold from 500K to 100K for `ml.inference.requests` | Priya Nair | P1 | 2025-11-14 | PLAT-4823 |
| Fix unbounded cache in feature-enrichment v2.14.3 (add LRU eviction, max_size=10000, TTL=5min) | Rahul Desai | P1 | 2025-11-14 | PLAT-4824 |
| Extend performance tests to 30-minute soak duration for memory-sensitive services | QA team | P2 | 2025-11-28 | PLAT-4825 |
| Add feature data freshness metric to recommendation service (expose age of newest feature vector) | Marcus Chen | P2 | 2025-11-28 | PLAT-4826 |
| Add recommendation staleness circuit breaker — degrade gracefully when feature age > 15 min | Architecture review required | P2 | 2025-12-12 | PLAT-4827 |
| Review canary rollout duration policy — minimum window should exceed P95 failure manifestation time | Platform Engineering | P3 | 2025-12-12 | PLAT-4828 |

---

## Appendix: Relevant Metrics

**Consumer lag growth rate during incident:**
```
ml_inference_consumer_lag{group="ml-inference-consumer"} 
  growing at ~2,000 msg/min between 14:05–15:10
  peak: 6.8M messages at 16:20
```

**OOM kill events:**
```
kube_pod_container_status_last_terminated_reason{reason="OOMKilled", 
  container="feature-enrichment"} 
  14 OOM kills between 14:05–15:10
```

**Enrichment pod memory at time of OOM:**
```
container_memory_working_set_bytes{container="feature-enrichment"}
  512Mi (limit) — consistent with unbounded cache reaching hard limit
```
