"""Deterministic builder for simulated incident telemetry.

Records flagged key=True are the evidence the evaluation expects a good investigation to
cite. The flag is only used to compute ground truth at seed time; it is never stored in
agent-readable tables.
"""

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.evidence import LEVELS, metric_evidence_id

SERVICES = {
    "api-gateway": "Public HTTPS entry point; routes /checkout, /login and /orders to backend services.",
    "checkout-api": "Builds carts and places orders; calls payments-api and inventory-api.",
    "payments-api": "Authorizes card payments; stores transactions in payments-db.",
    "payments-db": "PostgreSQL primary for payments (max_connections=100).",
    "auth-service": "Issues session tokens for /login; serves TLS on auth.internal.",
    "inventory-api": "Stock levels and reservations for checkout.",
    "notification-worker": "Sends order and marketing emails through an external email provider.",
}

@dataclass
class Log:
    service: str
    ts: datetime
    level: str
    message: str
    attributes: dict = field(default_factory=dict)
    key: bool = False


@dataclass
class Deployment:
    service: str
    version: str
    deployed_at: datetime
    status: str
    change_summary: str
    deployed_by: str
    key: bool = False


@dataclass
class Metric:
    service: str
    name: str
    unit: str
    points: list[tuple[datetime, float]]
    key: bool = False


@dataclass
class ScenarioData:
    slug: str
    title: str
    alert_summary: str
    incident_start: datetime
    logs: list[Log] = field(default_factory=list)
    deployments: list[Deployment] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)

    def log_refs(self) -> list[tuple[str, Log]]:
        ordered = sorted(self.logs, key=lambda log: (log.ts, log.service, log.message))
        return [(f"log:{index:04d}", log) for index, log in enumerate(ordered, start=1)]

    def deployment_refs(self) -> list[tuple[str, Deployment]]:
        ordered = sorted(self.deployments, key=lambda d: (d.deployed_at, d.service))
        return [(f"deploy:{index}", d) for index, d in enumerate(ordered, start=1)]

    def key_evidence_ids(self) -> list[str]:
        ids = [ref for ref, log in self.log_refs() if log.key]
        ids += [ref for ref, deployment in self.deployment_refs() if deployment.key]
        ids += [metric_evidence_id(m.service, m.name) for m in self.metrics if m.key]
        return ids


class Builder:
    """Helpers that turn a short incident description into a realistic timeline."""

    WINDOW_BEFORE = timedelta(minutes=120)
    WINDOW_AFTER = timedelta(minutes=60)
    STEP = timedelta(minutes=2)

    def __init__(self, slug: str, title: str, alert_summary: str, incident_start: datetime):
        self.data = ScenarioData(slug, title, alert_summary, incident_start)
        self.rng = random.Random(slug)
        self.t0 = incident_start

    def at(self, minutes: float) -> datetime:
        return self.t0 + timedelta(minutes=minutes)

    def log(self, service, minutes, level, message, key=False, **attributes):
        assert service in SERVICES and level in LEVELS
        self.data.logs.append(Log(service, self.at(minutes), level, message, attributes, key))

    def repeat(self, service, start, end, every, level, message, key_first=False, **attributes):
        """Emit a recurring log line with small jitter, e.g. a retry loop."""
        minute, first = start, True
        while minute < end:
            jitter = self.rng.uniform(0, every * 0.4)
            self.log(service, round(minute + jitter, 2), level, message, key=key_first and first, **attributes)
            minute += every
            first = False

    def noise(self, services, every=6.0):
        """Routine INFO traffic so incident signals must be found, not just read."""
        routine = [
            "GET /healthz 200 in {ms}ms",
            "request completed status=200 duration_ms={ms}",
            "cache refresh completed entries={n}",
            "processed batch size={n} duration_ms={ms}",
        ]
        for service in services:
            minute = -120.0 + self.rng.uniform(0, every)
            while minute < 60:
                template = self.rng.choice(routine)
                message = template.format(ms=self.rng.randint(4, 90), n=self.rng.randint(10, 500))
                self.log(service, round(minute, 2), "INFO", message)
                minute += every + self.rng.uniform(-1, 1)

    def deploy(self, service, minutes, version, change_summary, status="succeeded",
               deployed_by="ci-pipeline", key=False):
        self.data.deployments.append(
            Deployment(service, version, self.at(minutes), status, change_summary, deployed_by, key)
        )

    def series(self, service, name, unit, shape, key=False, noise=0.03):
        """shape(minute) -> value. Sampled every 2 minutes across the window, with jitter."""
        points = []
        minute = -120
        while minute <= 60:
            base = shape(minute)
            value = base * (1 + self.rng.uniform(-noise, noise)) if base else 0.0
            points.append((self.at(minute), round(max(value, 0.0), 3)))
            minute += 2
        self.data.metrics.append(Metric(service, name, unit, points, key))


def ramp(start, end, low, high):
    """Piecewise-linear helper: low before start, high after end, linear between."""

    def shape(minute):
        if minute <= start:
            return low
        if minute >= end:
            return high
        return low + (high - low) * (minute - start) / (end - start)

    return shape


def step(at, low, high):
    return lambda minute: high if minute >= at else low
