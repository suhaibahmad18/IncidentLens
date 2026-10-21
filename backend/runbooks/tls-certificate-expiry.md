# TLS certificate expiry

## Symptoms
- Clients or gateways log "x509: certificate has expired or is not yet valid" or "TLS handshake failed".
- Failures begin at a precise moment and affect every request to one hostname at once.
- The affected service's own request rate drops sharply, because handshakes fail before requests arrive; its CPU often falls.

## Likely causes
- Automated renewal (ACME / cert-manager) failed earlier, for example a DNS-01 or HTTP-01 challenge timeout, and nobody noticed the warning.
- A manually managed certificate was not rotated.
- Clock skew on clients (rare; failures would be partial and host-specific).

## How to confirm
1. Find the `notAfter` time of the certificate and compare it with when errors began.
2. Search logs from the hours before the incident for renewal failures or expiry warnings.
3. Check that the failures are TLS errors rather than application errors such as 401 responses.

## Remediation (advisory, requires human approval)
- Issue or renew the certificate, deploy it to the service, and confirm handshakes succeed.
- Fix the renewal path (DNS credentials, challenge reachability) so it does not recur.
- Add alerting on certificate expiry at least 14 days in advance.
