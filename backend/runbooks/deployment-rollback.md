# Correlating incidents with deployments and rolling back

## When a deployment is a suspect
- The first anomaly starts shortly after the deployment of the same service, or of a direct dependency.
- The change summary touches the failing area (database access, caching, configuration, TLS).
- Deployments of unrelated services, or deployments after the first symptom, are usually coincidences.

## How to check
1. List deployments in the hours before the incident with their exact times.
2. For each, ask: did this service's own metrics change first, and when?
3. Prefer the earliest change in the causal chain; services reporting upstream errors are often victims.

## Rollback (advisory, requires human approval)
- Roll back only the suspect service to its previous version, then watch the metric that first moved.
- Record the decision and the evidence in the incident channel.
- IncidentLens never executes rollbacks; it only recommends them for a human to perform.
