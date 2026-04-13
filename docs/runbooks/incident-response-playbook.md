# Incident Response Playbook

**Owner:** Platform Engineering  
**Last Updated:** 2025-10-14  
**Version:** 3.2

---

## Overview

This playbook defines the standard incident response process for all production systems at Meridian Data Corp. All on-call engineers are required to complete the IR certification before being added to the rotation.

---

## Severity Levels

### SEV-1 — Critical
- **Definition:** Complete outage of a customer-facing service or data loss event.
- **Response time:** Page on-call immediately. War room within 15 minutes.
- **Stakeholder notification:** VP Engineering + Director of Support notified within 20 minutes.
- **Examples:** Payment processing down, authentication service unreachable, Kafka cluster offline, database primary failure.

### SEV-2 — High
- **Definition:** Significant degradation of a core service. Partial outage or severe latency spike.
- **Response time:** On-call engineer acknowledges within 15 minutes.
- **Stakeholder notification:** Engineering manager notified. Support alerted if customer-visible.
- **Examples:** Search latency > 5s p99, recommendation engine returning stale results > 30 min, batch pipeline delayed > 2 hours.

### SEV-3 — Medium
- **Definition:** Non-critical service degraded. Workaround available.
- **Response time:** Acknowledged within 2 hours during business hours.
- **Examples:** Internal dashboard slow, non-critical job failure, single-region issue in a multi-region service.

### SEV-4 — Low
- **Definition:** Minor issue with no customer impact. Track in Jira.
- **Response time:** Next business day.

---

## On-Call Rotation

The on-call rotation covers three teams in a follow-the-sun model:

| Shift | Team | Hours (UTC) |
|---|---|---|
| Americas | Platform Engineering | 14:00 – 02:00 |
| EMEA | Infrastructure | 06:00 – 18:00 |
| APAC | Site Reliability | 22:00 – 10:00 |

Primary on-call is paged first. Secondary is paged if no acknowledgment within 10 minutes (SEV-1/2) or 30 minutes (SEV-3).

---

## Response Procedure

### Step 1 — Acknowledge and Triage
1. Acknowledge the PagerDuty alert to stop escalation.
2. Open the incident channel in Slack: `/incident create <short-description>`
3. Assign a severity level based on observed impact.
4. Assign an Incident Commander (IC) — the acknowledging engineer owns IC unless they transfer it.

### Step 2 — Assess Impact
- Check the Grafana dashboard: **Platform Overview** (bookmark: `grafana.internal/d/platform-overview`)
- Check error rates in Kibana: index pattern `prod-*`
- Confirm which services are affected using the service dependency map in Confluence.
- Run the blast radius script: `scripts/blast-radius.sh <service-name>`

### Step 3 — Communicate
- Post an initial status update to `#incidents` within 10 minutes of acknowledgment:
  > **[SEV-X] Short description** — Investigating. IC: @handle. Bridge: <link>

- Update every 20 minutes (SEV-1) or 30 minutes (SEV-2) until resolved.
- Use the Statuspage template for customer-facing communication — coordinate with Support lead.

### Step 4 — Mitigate
Prefer mitigation over root cause during active incident:
- Rollback to last known good deployment: `kubectl rollout undo deployment/<name> -n production`
- Enable feature flag kill switch via LaunchDarkly if applicable.
- Scale horizontally if resource-constrained: `kubectl scale deployment/<name> --replicas=<n>`
- Redirect traffic away from affected region via Route 53 failover if multi-region.

### Step 5 — Resolve
- Confirm resolution with at least 10 minutes of clean metrics before declaring resolved.
- Update Statuspage to "Resolved."
- Post resolution message to `#incidents`:
  > **[RESOLVED] Short description** — Resolved at HH:MM UTC. Root cause: <summary>. Postmortem scheduled for <date>.

### Step 6 — Postmortem
- SEV-1 and SEV-2 incidents require a postmortem within 5 business days.
- Use the postmortem template in Confluence: `Platform / Postmortems / Template`
- Schedule a 45-minute postmortem review meeting with IC, on-call team, and affected stakeholders.
- Postmortems are blameless. Focus on system and process failures, not individuals.

---

## Escalation Contacts

| Role | Name | Contact |
|---|---|---|
| VP Engineering | Rohan Mehta | PagerDuty: `rohan-mehta` |
| Director, Infrastructure | Sara Okonkwo | PagerDuty: `sara-okonkwo` |
| Security (if breach suspected) | SecOps team | PagerDuty: `secops-primary` |
| Database lead | Marcus Chen | PagerDuty: `dba-primary` |
| Kafka / Streaming lead | Priya Nair | PagerDuty: `streaming-oncall` |

---

## Useful Commands

```bash
# Check cluster node status
kubectl get nodes -o wide

# View recent events across all namespaces
kubectl get events --all-namespaces --sort-by='.lastTimestamp' | tail -50

# Check pod resource usage
kubectl top pods -n production --sort-by=memory

# Force-restart a deployment
kubectl rollout restart deployment/<name> -n production

# Drain a node safely
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data

# Check Kafka consumer group lag
kafka-consumer-groups.sh --bootstrap-server kafka.internal:9092 \
  --describe --group <group-name>

# Tail application logs (last 100 lines, follow)
kubectl logs -f deployment/<name> -n production --tail=100

# Check Milvus collection stats
python3 -c "from pymilvus import utility; print(utility.get_query_segment_info('<collection>'))"
```

---

## Common Failure Modes and Mitigations

### Kafka Consumer Lag Spike
**Symptoms:** Dashboards show consumer lag growing on topic `events.enriched` or `ml.inference.requests`.
**Check:** `kafka-consumer-groups.sh` for lag. Check consumer pod CPU/memory.
**Mitigation:** Increase consumer replicas. If rebalancing loop, restart consumer group coordinator pod.
**Do not:** Increase partition count in production without DBA approval — this causes full rebalance.

### Redis Cache Eviction Storm
**Symptoms:** Sudden spike in cache miss rate. Latency spikes across dependent services.
**Check:** Redis INFO stats — `maxmemory_policy`, `evicted_keys`, `used_memory`.
**Mitigation:** Increase Redis memory allocation or switch to LRU eviction. Warm cache from snapshot if available.

### Milvus Query Timeout
**Symptoms:** Vector search queries returning 503 or timing out. `search_latency_p99` > 2000ms in Grafana.
**Check:** Milvus query node pod memory. Collection segment load status.
**Mitigation:** Restart query node pods. If collection is too large for memory, enable disk-based index (DiskANN).

### PostgreSQL Replication Lag
**Symptoms:** Read replicas returning stale data. Lag metric > 30 seconds.
**Check:** `SELECT now() - pg_last_xact_replay_timestamp() AS lag;` on replica.
**Mitigation:** Route read traffic back to primary temporarily. Check WAL sender on primary.

---

## Post-Incident Checklist

- [ ] PagerDuty alert resolved
- [ ] Statuspage updated to Resolved
- [ ] Slack `#incidents` updated with resolution summary
- [ ] Jira ticket created with SEV label and all timeline notes
- [ ] Postmortem document created in Confluence (SEV-1/2)
- [ ] Postmortem meeting scheduled
- [ ] Action items created in Jira with owners and due dates
- [ ] Runbook updated if new failure mode discovered
