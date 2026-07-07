"""Labeled synthetic incident dataset used for demos and the eval harness.

Each incident has a ground-truth owning specialist + root cause + reference
remediation so we can score accuracy for both the multi-agent system and the
single-agent baseline. `cross_cutting=True` incidents are deliberately written
to plausibly implicate two domains, to exercise the negotiation protocol.
"""

from __future__ import annotations

from app.models import Incident, Specialist

INCIDENTS: list[Incident] = [
    Incident(
        id="inc-01",
        title="Admin login from unrecognized IP",
        description=(
            "Unusual spike in failed login attempts followed by a successful admin "
            "login from an unrecognized IP address. Audit logs show an API token "
            "with elevated scope was used moments before the login succeeded."
        ),
        ground_truth_specialist=Specialist.SECURITY,
        ground_truth_root_cause="Leaked/stolen API credential used for unauthorized admin access.",
        reference_remediation="Revoke and rotate the exposed token, force re-auth on admin sessions, enable MFA and IP allowlisting for admin accounts.",
    ),
    Incident(
        id="inc-02",
        title="p99 latency spike after deploy",
        description=(
            "API p99 latency jumped from 120ms to 4200ms immediately after the last "
            "deploy. CPU is pinned at 95% across all application servers. Error rate "
            "and traffic volume are unchanged."
        ),
        ground_truth_specialist=Specialist.PERFORMANCE,
        ground_truth_root_cause="Inefficient code path introduced in the latest deploy causing CPU saturation.",
        reference_remediation="Roll back the deploy, profile the new hot path, and add CPU-based autoscaling as a safety net.",
    ),
    Incident(
        id="inc-03",
        title="Checkout timeouts with rising deadlocks",
        description=(
            "Checkout service is throwing intermittent timeouts. Database metrics show "
            "growing lock wait times and a spike in deadlocks specifically on the "
            "orders table over the last 30 minutes."
        ),
        ground_truth_specialist=Specialist.DATABASE,
        ground_truth_root_cause="A long-running transaction is holding row locks on the orders table, causing cascading deadlocks.",
        reference_remediation="Kill the long-running transaction, add a covering index to shorten lock duration, and reduce transaction scope in the checkout path.",
    ),
    Incident(
        id="inc-04",
        title="EU users hit connection resets",
        description=(
            "Users in the EU region report intermittent connection resets. Load "
            "balancer health checks are flapping between healthy and unhealthy on "
            "several nodes, and cross-AZ links show elevated packet loss."
        ),
        ground_truth_specialist=Specialist.NETWORKING,
        ground_truth_root_cause="Packet loss on cross-availability-zone links is causing load balancer health checks to flap.",
        reference_remediation="Fail traffic over to the healthy AZ, engage the cloud provider's network team, and add client-side retry/backoff.",
    ),
    Incident(
        id="inc-05",
        title="Blank dashboard on Safari",
        description=(
            "The dashboard renders blank for users on Safari. Browser console shows a "
            "JS TypeError thrown from bundle.js immediately after the last frontend "
            "deploy. Backend API responses look healthy."
        ),
        ground_truth_specialist=Specialist.FRONTEND,
        ground_truth_root_cause="A frontend bundle regression causes an uncaught JS exception specifically on Safari.",
        reference_remediation="Roll back the frontend deploy and add a cross-browser test matrix to CI to catch Safari-only regressions.",
    ),
    Incident(
        id="inc-06",
        title="Auth service slow during traffic burst",
        description=(
            "API latency spiked at the same moment a burst of malformed requests hit "
            "the login endpoint. WAF logs show a credential-stuffing pattern "
            "overwhelming the auth service, causing CPU exhaustion and slow "
            "responses site-wide."
        ),
        ground_truth_specialist=Specialist.SECURITY,
        ground_truth_root_cause="A credential-stuffing attack against the login endpoint is overwhelming the auth service and exhausting CPU.",
        reference_remediation="Rate-limit and block the offending IPs at the WAF, add a CAPTCHA challenge on repeated failures, then scale the auth service back down.",
        cross_cutting=True,
    ),
    Incident(
        id="inc-07",
        title="Gradual checkout slowdown, no deploy",
        description=(
            "Checkout latency degraded gradually over two hours with no recent "
            "deploy. Application server CPU is normal, but the database connection "
            "pool is exhausted and query times have tripled."
        ),
        ground_truth_specialist=Specialist.DATABASE,
        ground_truth_root_cause="Slow queries (from stale statistics/missing index) are exhausting the database connection pool.",
        reference_remediation="Identify and optimize the slow queries, rebuild table statistics/indexes, and add connection pool saturation alerts.",
        cross_cutting=True,
    ),
    Incident(
        id="inc-08",
        title="Replica lag alarms with stale reads",
        description=(
            "Replica lag alarms are firing and reads from the replica occasionally "
            "return stale data. The VPC peering link between the primary and replica "
            "region shows increased latency and intermittent drops."
        ),
        ground_truth_specialist=Specialist.NETWORKING,
        ground_truth_root_cause="Degradation on the cross-region VPC peering link is delaying replication traffic.",
        reference_remediation="Engage the network provider on the peering link, temporarily route replication over a backup path, and widen replica-lag alert thresholds during the incident.",
        cross_cutting=True,
    ),
    Incident(
        id="inc-09",
        title="Static assets failing to load in one region",
        description=(
            "Users report the app is unusable. The frontend shows repeated failed "
            "asset loads (404/timeout) for static JS/CSS files. CDN edge nodes in one "
            "region are returning stale or failed responses."
        ),
        ground_truth_specialist=Specialist.NETWORKING,
        ground_truth_root_cause="A CDN edge node cache/config issue in one region is failing to serve static assets.",
        reference_remediation="Purge and re-warm the affected CDN edge cache, temporarily remove the unhealthy edge node from rotation, and verify asset origin health.",
        cross_cutting=True,
    ),
    Incident(
        id="inc-10",
        title="Publicly exposed storage bucket with PII",
        description=(
            "A security scanner flags a publicly exposed object storage bucket "
            "containing customer PII. Access logs show anonymous read requests from "
            "external IP addresses over the past week."
        ),
        ground_truth_specialist=Specialist.SECURITY,
        ground_truth_root_cause="A misconfigured bucket access policy exposed customer PII to anonymous public reads.",
        reference_remediation="Lock down the bucket policy to private, rotate any secrets that may have been exposed, and notify compliance/legal per breach policy.",
    ),
    Incident(
        id="inc-11",
        title="Job queue backlog with worker OOM loop",
        description=(
            "The background job queue backlog is growing unbounded. Worker processes "
            "show steadily increasing memory usage until they are OOM-killed and "
            "restarted, repeatedly."
        ),
        ground_truth_specialist=Specialist.PERFORMANCE,
        ground_truth_root_cause="A memory leak in the worker's job handler is causing an OOM crash loop.",
        reference_remediation="Patch the leaking handler, cap worker memory with alerts, and add backpressure so the queue sheds load instead of growing unbounded.",
    ),
    Incident(
        id="inc-12",
        title="Odd slow queries against customer table",
        description=(
            "Suspicious slow queries are hitting the customer table from an "
            "application role that normally only reads the orders table. The query "
            "patterns resemble SQL injection reconnaissance, and the requests are "
            "coming from an unauthorized service account attempting to exploit a "
            "parameterization gap."
        ),
        ground_truth_specialist=Specialist.SECURITY,
        ground_truth_root_cause="A SQL injection attempt is probing the customer table through an over-privileged application role.",
        reference_remediation="Block the offending requests, patch the vulnerable query path with parameterization, and scope the application role's DB permissions down to least privilege.",
        cross_cutting=True,
    ),
]


def get_incident(incident_id: str) -> Incident:
    for incident in INCIDENTS:
        if incident.id == incident_id:
            return incident
    raise KeyError(f"Unknown incident id: {incident_id}")
