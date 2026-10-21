# Queue backlog and third-party rate limiting

## Symptoms
- `queue_depth` grows continuously while consumers appear healthy.
- Worker logs show HTTP 429 "Too Many Requests" or provider "rate limit exceeded" responses, with retries.
- Throughput to the provider drops to the provider's allowed rate; messages are delayed or dropped after retries.

## Likely causes
- A burst of work, such as a bulk or marketing job, sharing the same provider quota as time-sensitive messages.
- A lowered provider quota or a plan change.
- Retry storms: aggressive retries without backoff consume the quota further.

## How to confirm
1. Find what was enqueued just before the backlog started (bulk jobs, campaigns, replays).
2. Confirm 429 responses began at the same time and that the retry rate rose.
3. Rule out a recent deployment of the worker or of the services producing messages by checking timing and logs.

## Remediation (advisory, requires human approval)
- Pause or throttle the bulk job; prioritise transactional messages on a separate queue or quota.
- Apply exponential backoff with jitter to retries.
- Ask the provider for a temporary quota increase if business-critical messages are delayed.
