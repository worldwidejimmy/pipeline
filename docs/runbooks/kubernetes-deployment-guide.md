# Kubernetes Deployment Guide — Platform Engineering

**Owner:** Platform Engineering  
**Last Updated:** 2025-09-20  
**Cluster:** `prod-us-west2` (GKE), `prod-us-east1` (GKE), `staging-us-central1` (GKE)

---

## Overview

This guide covers the standard deployment workflow for services on the Meridian Kubernetes clusters. All production services must follow this process. Deviations require Platform Engineering approval.

---

## Cluster Access

### Kubeconfig Setup

```bash
# Production west
gcloud container clusters get-credentials prod-us-west2 \
  --region us-west2 --project meridian-prod

# Production east
gcloud container clusters get-credentials prod-us-east1 \
  --region us-east1 --project meridian-prod

# Staging
gcloud container clusters get-credentials staging-us-central1 \
  --region us-central1 --project meridian-staging

# Switch contexts
kubectl config use-context gke_meridian-prod_us-west2_prod-us-west2
```

### Namespace Conventions

| Namespace | Purpose |
|---|---|
| `production` | All customer-facing services |
| `ml-inference` | ML model serving (vLLM, embedding services) |
| `data-platform` | Data pipeline services (Kafka, Airflow workers) |
| `monitoring` | Prometheus, Grafana, Alertmanager |
| `ingress` | NGINX ingress controllers, cert-manager |
| `staging` | All staging workloads |

---

## Standard Deployment Workflow

### 1. Build and Push Container Image

All images are built via Jenkins CI and pushed to the internal Artifact Registry:

```
us-docker.pkg.dev/meridian-prod/services/<service-name>:<git-sha>
```

**Never use `latest` tag in production deployments.** Always use the immutable Git SHA tag.

```bash
# Build locally for testing
docker build -t us-docker.pkg.dev/meridian-prod/services/my-service:$(git rev-parse HEAD) .

# Push (CI does this automatically — only run manually if needed)
docker push us-docker.pkg.dev/meridian-prod/services/my-service:$(git rev-parse HEAD)
```

### 2. Update Helm Values

All services are deployed via Helm. Values files live in the `k8s/` directory of each service repo.

```bash
# Staging
helm upgrade --install my-service ./helm/my-service \
  -f helm/my-service/values-staging.yaml \
  --set image.tag=$(git rev-parse HEAD) \
  --namespace staging \
  --wait --timeout 5m

# Production (requires two-person rule approval in Jira)
helm upgrade --install my-service ./helm/my-service \
  -f helm/my-service/values-production.yaml \
  --set image.tag=$(git rev-parse HEAD) \
  --namespace production \
  --wait --timeout 5m
```

### 3. Canary Rollout (Required for Production)

All production deployments use a canary strategy. The standard canary progression:

```
5% canary → 10 min soak → 25% → 10 min soak → 100%
```

Canary is managed via the Argo Rollouts controller:

```yaml
# rollout.yaml (excerpt)
spec:
  strategy:
    canary:
      steps:
      - setWeight: 5
      - pause: {duration: 10m}
      - setWeight: 25
      - pause: {duration: 10m}
      - setWeight: 100
      canaryMetadata:
        labels:
          role: canary
      stableMetadata:
        labels:
          role: stable
      analysis:
        templates:
        - templateName: success-rate
        startingStep: 1
```

**Auto-rollback:** Argo Rollouts automatically rolls back if:
- Error rate > 2% during canary window
- p99 latency > 2x baseline during canary window
- Readiness probe fails for > 2 consecutive minutes

### 4. Verify Deployment

```bash
# Check rollout status
kubectl argo rollouts status my-service -n production

# Watch rollout progress
kubectl argo rollouts watch my-service -n production

# Check pod status
kubectl get pods -n production -l app=my-service

# Check recent events
kubectl describe deployment my-service -n production | tail -20

# Check logs
kubectl logs -l app=my-service -n production --tail=50 -f
```

### 5. Rollback

```bash
# Rollback via Argo Rollouts (preferred — respects canary logic)
kubectl argo rollouts abort my-service -n production
kubectl argo rollouts undo my-service -n production

# Emergency rollback via kubectl (bypasses canary)
kubectl rollout undo deployment/my-service -n production

# Rollback to specific revision
kubectl rollout undo deployment/my-service -n production --to-revision=3
```

---

## Resource Requirements

All production deployments must specify resource requests and limits.

### Standard Tiers

| Tier | CPU Request | CPU Limit | Memory Request | Memory Limit | Use For |
|---|---|---|---|---|---|
| XS | 50m | 200m | 64Mi | 128Mi | Sidecars, health check services |
| S | 100m | 500m | 128Mi | 256Mi | Lightweight API services |
| M | 250m | 1000m | 512Mi | 1Gi | Standard API services |
| L | 500m | 2000m | 1Gi | 2Gi | CPU-intensive services |
| XL | 1000m | 4000m | 2Gi | 4Gi | Memory-intensive services |
| GPU | 1000m | 4000m | 8Gi | 16Gi + GPU | ML inference |

**VPA (Vertical Pod Autoscaler):** Enabled in recommendation mode for all production services. Review VPA recommendations monthly via `kubectl describe vpa <service> -n production`.

**HPA (Horizontal Pod Autoscaler):** Required for any service expecting variable load.

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: my-service
  namespace: production
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: my-service
  minReplicas: 3
  maxReplicas: 50
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Pods
    pods:
      metric:
        name: http_requests_per_second
      target:
        type: AverageValue
        averageValue: "100"
```

---

## Health Checks

Every service must define liveness and readiness probes.

```yaml
livenessProbe:
  httpGet:
    path: /health/live
    port: 8080
  initialDelaySeconds: 30
  periodSeconds: 10
  failureThreshold: 3
  timeoutSeconds: 5

readinessProbe:
  httpGet:
    path: /health/ready
    port: 8080
  initialDelaySeconds: 10
  periodSeconds: 5
  failureThreshold: 3
  timeoutSeconds: 3
```

**Liveness vs Readiness:**
- **Liveness:** Is the process alive? Failure restarts the pod.
- **Readiness:** Is the pod ready to serve traffic? Failure removes from Service endpoints.

Add a startup probe for services with slow initialization (e.g., model loading):

```yaml
startupProbe:
  httpGet:
    path: /health/ready
    port: 8080
  failureThreshold: 30
  periodSeconds: 10   # Allows up to 5 minutes for startup
```

---

## Secrets Management

All secrets are stored in HashiCorp Vault and injected via the Vault Agent Injector.

```yaml
# Pod annotation to inject secrets
annotations:
  vault.hashicorp.com/agent-inject: "true"
  vault.hashicorp.com/role: "my-service"
  vault.hashicorp.com/agent-inject-secret-config: "secret/my-service/config"
  vault.hashicorp.com/agent-inject-template-config: |
    {{- with secret "secret/my-service/config" -}}
    OPENAI_API_KEY={{ .Data.data.openai_api_key }}
    DATABASE_URL={{ .Data.data.database_url }}
    {{- end }}
```

**Never:**
- Commit secrets to Git
- Use Kubernetes Secrets directly without Vault backing
- Log environment variables (may contain secrets)
- Store secrets in ConfigMaps

---

## GPU Workloads (ML Inference)

GPU nodes use the `ml-inference` namespace and require specific node selection:

```yaml
nodeSelector:
  cloud.google.com/gke-accelerator: nvidia-tesla-a100

tolerations:
- key: "nvidia.com/gpu"
  operator: "Exists"
  effect: "NoSchedule"

resources:
  limits:
    nvidia.com/gpu: "4"
```

GPU node pool: `ml-gpu-pool` — 8 nodes, 4x A100 80GB each.

GPU scheduling uses `nvidia-device-plugin`. Monitor GPU utilization via DCGM Exporter metrics in Grafana (dashboard: "GPU Cluster Overview").

**Time-slicing:** Not enabled. Each pod gets exclusive GPU allocation to avoid memory contention during inference.

---

## Cluster Maintenance Windows

Production clusters have scheduled maintenance windows:

- **GKE auto-upgrades:** Sundays 02:00–06:00 UTC
- **Node pool replacement:** Coordinated with SRE. 72-hour advance notice required for changes to GPU node pools.
- **PodDisruptionBudgets:** Required for all production services with minAvailable: 1 (or 50% for > 4 replicas).

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: my-service-pdb
  namespace: production
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: my-service
```

---

## Troubleshooting Common Issues

### OOMKilled Pods

```bash
# Find OOMKilled pods
kubectl get events -n production | grep OOMKill
kubectl get pods -n production | grep OOMKill

# Check memory usage before setting new limits
kubectl top pods -n production -l app=my-service

# Check VPA recommendation
kubectl describe vpa my-service -n production
```

Set memory limits 1.5–2x the p99 working set observed under load.

### ImagePullBackOff

```bash
kubectl describe pod <pod-name> -n production | grep -A5 "Events:"
```
Common causes: image tag doesn't exist, Artifact Registry permissions, network policy blocking image pull.

### CrashLoopBackOff

```bash
# Check last crash reason
kubectl logs <pod-name> -n production --previous

# Check resource limits (OOM?)
kubectl describe pod <pod-name> -n production | grep -A3 "Last State:"
```

### Pending Pods

```bash
# Check why pod can't be scheduled
kubectl describe pod <pod-name> -n production | grep -A20 "Events:"
```
Common causes: insufficient resources on nodes, node selector mismatch, PVC not bound, taint/toleration mismatch.
