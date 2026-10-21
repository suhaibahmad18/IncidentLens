"""Simulated incidents. Titles and alert summaries describe symptoms only, never causes."""

from datetime import UTC, datetime

from app.seed_data.builder import Builder, ScenarioData, ramp, step


def checkout_db_pool_exhaustion() -> ScenarioData:
    b = Builder(
        "checkout-db-pool-exhaustion",
        "Checkout failures spike",
        "PagerDuty: api-gateway /checkout 5xx rate above 15% for 5 minutes; "
        "customers report 'payment could not be processed'.",
        datetime(2026, 3, 10, 14, 0, tzinfo=UTC),
    )
    b.noise(["api-gateway", "checkout-api", "payments-api", "inventory-api", "auth-service"])

    b.deploy("api-gateway", -150, "v1.9.2", "Update per-client rate-limit configuration")  # red herring
    b.deploy("payments-api", -20, "v2.14.0",
             "Add fraud-score lookup per authorization; refactor DB session handling", key=True)
    b.deploy("inventory-api", 25, "v3.1.4", "Dependency patch updates")  # after the incident

    b.repeat("payments-api", -12, -2, 2.5, "WARN",
             "db pool usage high in_use=88 max=100 waiting=3", pool="payments-db")
    b.repeat("payments-api", -2, 60, 1.5, "ERROR",
             "timeout acquiring connection from pool after 5000ms in_use=100 max=100",
             key_first=True, pool="payments-db")
    b.log("payments-db", -1, "FATAL",
          "remaining connection slots are reserved for non-replication superuser connections", key=True)
    b.repeat("payments-db", 0, 60, 4, "ERROR",
             "too many connections for role \"payments_app\"")
    b.repeat("checkout-api", 0, 60, 1.2, "ERROR",
             "payment authorization failed: upstream payments-api returned 503", order_flow="checkout")
    b.repeat("api-gateway", 0, 60, 1.0, "ERROR", "POST /checkout 502 upstream=checkout-api")
    b.log("payments-db", -60, "WARN", "checkpoint occurring too frequently (24 seconds apart)")  # benign

    b.series("payments-db", "db_connections_in_use", "count", ramp(-20, -1, 38, 100), key=True)
    b.series("payments-api", "p95_latency_ms", "ms", ramp(-12, 0, 140, 5100))
    b.series("payments-api", "http_error_rate", "percent", ramp(-2, 2, 0.2, 41))
    b.series("checkout-api", "http_error_rate", "percent", ramp(-1, 3, 0.3, 18))
    b.series("api-gateway", "http_error_rate", "percent", ramp(-1, 3, 0.4, 16.5))
    b.series("payments-db", "cpu_percent", "percent", ramp(-20, 0, 31, 44))
    b.series("inventory-api", "p95_latency_ms", "ms", lambda m: 85)
    return b.data


def auth_certificate_expiry() -> ScenarioData:
    b = Builder(
        "auth-certificate-expiry",
        "Login failures across web and mobile",
        "PagerDuty: api-gateway /login 5xx rate above 30%; synthetic login check failing in all regions.",
        datetime(2026, 4, 2, 9, 0, tzinfo=UTC),
    )
    b.noise(["api-gateway", "checkout-api", "auth-service", "notification-worker"])

    b.deploy("checkout-api", -40, "v4.1.0", "Copy changes on the cart page")  # red herring
    b.deploy("notification-worker", -95, "v1.7.3", "Retry jitter tweak")  # red herring

    b.log("auth-service", -105, "WARN",
          "certificate renewal failed for auth.internal: DNS-01 challenge timed out; will retry in 6h",
          key=True, issuer="internal-acme")
    b.log("auth-service", -60, "WARN", "TLS certificate for auth.internal expires in 60m (notAfter=2026-04-02T09:00:00Z)")
    b.repeat("api-gateway", 0, 60, 0.8, "ERROR",
             "upstream auth-service TLS handshake failed: x509: certificate has expired or is not yet valid",
             key_first=True, route="/login")
    b.repeat("auth-service", 0, 60, 3, "WARN", "client closed connection during TLS handshake (bad certificate)")
    b.log("checkout-api", -30, "WARN", "slow query on cart lookup duration_ms=820")  # benign

    b.series("api-gateway", "http_error_rate", "percent", step(0, 0.3, 34), key=True)
    b.series("auth-service", "request_rate", "rps", step(0, 220, 3))
    b.series("auth-service", "cpu_percent", "percent", lambda m: 22 if m < 0 else 9)
    b.series("checkout-api", "http_error_rate", "percent", lambda m: 0.4)
    return b.data


def inventory_memory_leak() -> ScenarioData:
    b = Builder(
        "inventory-memory-leak",
        "Checkout latency and intermittent stock errors",
        "PagerDuty: checkout-api p95 latency above 3s; intermittent 'unable to reserve stock' errors.",
        datetime(2026, 5, 18, 20, 0, tzinfo=UTC),
    )
    b.noise(["api-gateway", "checkout-api", "inventory-api", "payments-api"])

    b.deploy("inventory-api", -115, "v3.2.0",
             "Add in-process product cache for stock lookups", key=True)
    b.deploy("payments-api", -35, "v2.13.2", "Log format change")  # red herring

    b.repeat("inventory-api", -40, -5, 7, "WARN", "GC pause 1.2s heap_used=1.7GiB heap_limit=2.0GiB")
    b.log("inventory-api", -4, "ERROR", "container inventory-api-7c9f OOMKilled: memory limit 2048Mi exceeded",
          key=True, restart_count=1)
    b.repeat("inventory-api", 6, 60, 11, "ERROR", "container OOMKilled: memory limit 2048Mi exceeded")
    b.repeat("checkout-api", -3, 60, 1.5, "ERROR",
             "stock reservation failed: inventory-api timeout after 3000ms", key_first=True)
    b.log("payments-api", -50, "WARN", "autovacuum running on table transactions")  # benign

    b.series("inventory-api", "memory_usage_mb", "MB",
             lambda m: 520 + 1510 * max(0.0, (m + 115) / 111) if m < -4 else 600 + 140 * ((m + 4) % 10),
             key=True)
    b.series("inventory-api", "pod_restarts", "count",
             lambda m: 0 if m < -4 else 1 + int((m + 4) // 10))
    b.series("inventory-api", "p95_latency_ms", "ms", ramp(-60, -4, 60, 2900))
    b.series("checkout-api", "p95_latency_ms", "ms", ramp(-40, 0, 300, 3400))
    b.series("payments-api", "p95_latency_ms", "ms", lambda m: 150)
    return b.data


def notification_email_backlog() -> ScenarioData:
    """Held-out scenario: never used when tuning prompts or tools."""
    b = Builder(
        "notification-email-backlog",
        "Order confirmation emails delayed",
        "Support tickets: customers not receiving order confirmation emails; queue age alert on notifications.",
        datetime(2026, 6, 7, 16, 0, tzinfo=UTC),
    )
    b.noise(["api-gateway", "checkout-api", "notification-worker", "payments-api"])

    b.deploy("checkout-api", -20, "v4.3.0", "Order confirmation email template tweak")  # tempting red herring
    b.log("notification-worker", -6, "INFO",
          "accepted bulk campaign 'summer-sale' recipients=250000 priority=normal", key=True)
    b.repeat("notification-worker", -3, 60, 1.0, "WARN",
             "email provider responded 429 Too Many Requests; retrying in 30s", key_first=True,
             provider="mailrelay")
    b.repeat("notification-worker", 10, 60, 4, "ERROR",
             "dropping message after 5 retries: provider rate limit exceeded")
    b.log("checkout-api", -18, "INFO", "email template order_confirmation v12 loaded")

    b.series("notification-worker", "queue_depth", "messages", ramp(-6, 50, 20, 182000), key=True)
    b.series("notification-worker", "emails_sent_per_min", "count", lambda m: 900 if m < -3 else 600)
    b.series("checkout-api", "http_error_rate", "percent", lambda m: 0.3)
    return b.data


ALL_SCENARIOS = (
    checkout_db_pool_exhaustion,
    auth_certificate_expiry,
    inventory_memory_leak,
    notification_email_backlog,
)
