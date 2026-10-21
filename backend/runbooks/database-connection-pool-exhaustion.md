# Database connection pool exhaustion

## Symptoms
- Application logs show "timeout acquiring connection from pool" or "pool exhausted".
- PostgreSQL logs "remaining connection slots are reserved" or "too many connections".
- `db_connections_in_use` sits at or near `max_connections` and does not drop between requests.
- Callers of the affected service see 503s and rising p95 latency; downstream services report upstream errors.

## Likely causes
- A recent deployment that leaks connections: sessions opened per request but not returned on error paths, or new per-request queries holding a connection while calling another service.
- A sudden traffic increase with a pool sized too small.
- Long-running transactions or locks holding connections.

## How to confirm
1. Compare the time the connection count started climbing with recent deployments of services that use the database.
2. Check whether connections climb steadily (leak) or jump with request rate (capacity).
3. Look for the earliest pool warnings; the first service to log them is usually the source, not the services reporting upstream 5xx.

## Remediation (advisory, requires human approval)
- Roll back the suspect deployment if the climb started right after it.
- As a temporary measure, restart the leaking service to release connections.
- Do not simply raise `max_connections` without finding the leak; it delays the failure and can overload the database.
