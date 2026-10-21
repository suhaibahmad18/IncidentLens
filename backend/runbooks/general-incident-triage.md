# General incident triage

## First five minutes
- Read the alert: which user-facing symptom, which service reported it, since when.
- Establish the timeline: when did the first metric move, and what changed shortly before (deployments, jobs, certificates, traffic)?
- Separate the source from the victims: follow upstream errors to the first service that failed on its own.

## Gathering evidence
- Logs: look for the earliest WARN/ERROR/FATAL lines near the start of the incident, not only the loudest ones.
- Metrics: compare the baseline before the incident with the peak during it; check error rate, latency, saturation (connections, memory, queue depth) and traffic.
- Deployments: list recent changes for the affected service and its dependencies.

## Stating conclusions
- Name one most likely root cause and the service where it originates.
- Cite the specific log lines, metrics and changes that support it.
- If the evidence does not clearly point to a cause, say the investigation is inconclusive and list what to check next.
- Recommend remediation for a human to approve; never claim an action was taken.
