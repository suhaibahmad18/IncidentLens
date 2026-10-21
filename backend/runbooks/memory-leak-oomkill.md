# Memory leak and OOMKilled containers

## Symptoms
- `memory_usage_mb` rises steadily over minutes or hours instead of staying flat.
- Long garbage-collection pauses are logged as the heap approaches its limit.
- Containers are OOMKilled and restart; `pod_restarts` increases in a sawtooth pattern.
- Callers see timeouts while instances restart or pause.

## Likely causes
- A new in-process cache or collection without eviction or size limits.
- Objects retained by listeners, closures or global registries.
- A traffic change that increases per-request memory beyond the configured limit.

## How to confirm
1. Find when memory growth started and compare it with deployments of the same service.
2. Distinguish a leak (steady growth independent of traffic) from load (memory tracks request rate).
3. Check whether restarts temporarily reset memory and the growth repeats.

## Remediation (advisory, requires human approval)
- Roll back the deployment that introduced the growth.
- If a rollback is not possible, raise the memory limit temporarily and schedule restarts while a fix is prepared.
- Add bounds and eviction to caches; add memory alerts based on growth rate.
