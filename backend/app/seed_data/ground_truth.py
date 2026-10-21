"""Hidden answers for evaluation. Imported ONLY by the seed command.

Stored in scenario_ground_truth, which no application role can read. Never import this
module from agent, tool, API or frontend-facing code (a test enforces this).
"""

GROUND_TRUTH = {
    "checkout-db-pool-exhaustion": {
        "split": "dev",
        "service": "payments-api",
        "root_cause": "payments-api v2.14.0 deployment leaks database connections, exhausting the "
        "payments-db connection pool; checkout and gateway errors are downstream effects.",
        "keywords": [["connection", "pool"], ["v2.14", "deploy", "release", "leak"]],
    },
    "auth-certificate-expiry": {
        "split": "dev",
        "service": "auth-service",
        "root_cause": "The auth-service TLS certificate for auth.internal expired after automated "
        "renewal failed (DNS-01 challenge timeout), so gateway TLS handshakes fail.",
        "keywords": [["certificate", "cert", "tls", "x509"], ["expir"]],
    },
    "inventory-memory-leak": {
        "split": "dev",
        "service": "inventory-api",
        "root_cause": "inventory-api v3.2.0 added an unbounded in-process product cache that leaks "
        "memory until containers are OOMKilled, causing checkout stock-reservation timeouts.",
        "keywords": [["memory", "oom", "heap"], ["cache", "v3.2", "deploy", "leak"]],
    },
    "notification-email-backlog": {
        "split": "holdout",
        "service": "notification-worker",
        "root_cause": "A bulk 'summer-sale' marketing campaign exceeded the email provider's rate "
        "limit (HTTP 429), so notification-worker retries back up the queue and delay "
        "order confirmation emails.",
        "keywords": [["rate limit", "rate-limit", "429", "too many requests", "throttl"],
                     ["campaign", "bulk", "summer-sale", "marketing"]],
    },
}
