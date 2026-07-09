"""Labeled synthetic incident dataset used for demos and the eval harness.

Each incident starts as a short `alert` (what an on-call engineer sees first) plus a
set of per-domain `tools` -- each one simulating a dashboard/log system that domain
would check, returning a canned result. Most tools return a "clean" result; one (or
sometimes two, for a genuinely elevated-but-misleading reading) contains the real
signal. A domain with no tool listed has no distinct monitoring channel relevant to
that incident at all -- realistic, since not every incident touches every system.
"""

from __future__ import annotations

from app.models import Incident, Specialist, ToolDefinition


def _tool(description: str, result: str) -> ToolDefinition:
    return ToolDefinition(description=description, result=result)


INCIDENTS: list[Incident] = [
    Incident(
        id="inc-01",
        title="orders-api intermittent timeouts, unresolved live incident",
        alert=(
            "PagerDuty ALERT: orders-api p99 latency > 2000ms (threshold 500ms), error rate "
            "14.8% (baseline 0.2%), ongoing since 09:00 UTC. Approximately 15% of requests are "
            "timing out; the remaining 85% complete normally and quickly."
        ),
        tools={
            Specialist.PERFORMANCE: _tool(
                "Check CPU/memory resource metrics for orders-api pods and cluster nodes.",
                "CPU and memory on all currently-provisioned orders-api pods: nominal (under "
                "40% utilization). Node-level CPU/memory/disk across the cluster: nominal.",
            ),
            Specialist.DATABASE: _tool(
                "Check database connection pool, replication lag, and query performance.",
                "Connection pool utilization ~40% (normal). Replication lag 0.3s (normal). No "
                "slow queries detected.",
            ),
            Specialist.SECURITY: _tool(
                "Check authentication logs, WAF logs, and traffic patterns for anomalies.",
                "No anomalous authentication attempts. No WAF block spike. Traffic pattern "
                "composition normal for time of day.",
            ),
            Specialist.NETWORKING: _tool(
                "Check recent infrastructure changes: decommissions, DNS records, network config.",
                "Nightly capacity-optimization job 'decom-orders-api-node-7' completed "
                "successfully at 08:45:12 UTC. Internal DNS record 'orders-api.internal' has "
                "TTL=86400s (24h) and was last modified 6 days ago -- it was NOT updated as "
                "part of the decommission process.",
            ),
        },
        ground_truth_specialist=Specialist.NETWORKING,
        ground_truth_root_cause=(
            "A decommissioned backend server continues receiving ~15% of traffic because some "
            "DNS resolver caches have not yet expired the 24-hour TTL for the internal service "
            "record pointing to it; requests routed to the decommissioned host time out."
        ),
        reference_remediation=(
            "Update or remove the DNS record for the decommissioned host immediately and force "
            "propagation; going forward, lower the TTL before planned decommissions or drain "
            "traffic for a full TTL period before removing capacity."
        ),
        cross_cutting=True,
    ),
    Incident(
        id="inc-02",
        title="checkout-service error rate spike with elevated resource usage",
        alert=(
            "PagerDuty ALERT: checkout-service error rate spiked from 0.1% to 22% starting "
            "14:10 UTC. p99 latency also elevated (180ms -> 900ms). No code deploy in the "
            "last 5 days."
        ),
        tools={
            Specialist.PERFORMANCE: _tool(
                "Check CPU/memory resource metrics for checkout-service pods.",
                "CPU on checkout-service pods elevated to ~70% (baseline ~30%), not yet at "
                "capacity limits. Memory usage nominal. No pod restarts or OOM events.",
            ),
            Specialist.DATABASE: _tool(
                "Check database connection pool, replication lag, and query performance.",
                "Connection pool utilization elevated to ~75% (baseline ~35%). No slow queries "
                "detected, no replication lag, no schema or index changes recently.",
            ),
            Specialist.NETWORKING: _tool(
                "Check network/DNS/load-balancer/infrastructure status.",
                "No packet loss, no DNS issues, load balancer health checks all passing, no "
                "infrastructure changes in the last 24 hours.",
            ),
            Specialist.SECURITY: _tool(
                "Check authentication logs, WAF logs, and traffic patterns for security anomalies.",
                "WAF logs show a sustained burst of malformed authentication requests against "
                "the checkout-service's payment-token validation endpoint, beginning at 14:10 "
                "UTC. Each malformed request triggers a full cryptographic validation pass "
                "before being rejected, consuming CPU and database lookups disproportionate to "
                "normal traffic. Pattern is consistent with automated credential/token-guessing.",
            ),
        },
        ground_truth_specialist=Specialist.SECURITY,
        ground_truth_root_cause=(
            "A sustained automated credential/token-guessing attack against the payment-token "
            "validation endpoint forces expensive cryptographic checks on every malformed "
            "request, exhausting CPU and database connections as a downstream symptom."
        ),
        reference_remediation=(
            "Block the offending source IPs at the WAF, add rate limiting on the payment-token "
            "validation endpoint, and reject malformed requests before the expensive "
            "cryptographic validation step."
        ),
        cross_cutting=True,
    ),
    Incident(
        id="inc-03",
        title="checkout deadlocks with rising lock wait times",
        alert=(
            "PagerDuty ALERT: checkout-service intermittent timeouts, correlated with a spike "
            "in database deadlocks over the last 30 minutes."
        ),
        tools={
            Specialist.PERFORMANCE: _tool(
                "Check CPU/memory resource metrics for checkout-service pods.",
                "CPU and memory on checkout-service pods: nominal. No recent deploys. Traffic "
                "volume unchanged from baseline.",
            ),
            Specialist.DATABASE: _tool(
                "Check database locking, deadlocks, and transaction activity.",
                "A spike in deadlocks specifically on the orders table over the last 30 "
                "minutes. Lock wait times climbing. Active session trace shows two long-running "
                "transactions acquiring row locks on the orders table in inconsistent order "
                "during inventory reservation and order insertion.",
            ),
            Specialist.NETWORKING: _tool(
                "Check network/DNS/load-balancer/infrastructure status.",
                "No packet loss, no DNS changes, no infrastructure changes in the last 24 hours.",
            ),
        },
        ground_truth_specialist=Specialist.DATABASE,
        ground_truth_root_cause=(
            "Concurrent checkout transactions acquire row-level locks on the orders table in "
            "inconsistent order, causing a classic deadlock cycle that produces cascading lock "
            "wait timeouts."
        ),
        reference_remediation=(
            "Enforce a consistent lock acquisition order across all checkout code paths, "
            "reduce transaction scope, add retry logic with backoff for deadlock victims, and "
            "add a covering index to shorten lock duration."
        ),
        cross_cutting=False,
    ),
    Incident(
        id="inc-04",
        title="dashboard blank for users on Safari after latest deploy",
        alert=(
            "Support tickets: multiple users report the dashboard renders blank on Safari "
            "since this morning's frontend deploy. Backend API responses look healthy."
        ),
        tools={
            Specialist.FRONTEND: _tool(
                "Check browser console errors and client-side error tracking.",
                "Error tracking shows a spike in uncaught TypeErrors thrown from bundle.js, "
                "Safari only, starting immediately after today's frontend deploy (14:02 UTC). "
                "Chrome and Firefox show no errors.",
            ),
            Specialist.PERFORMANCE: _tool(
                "Check CPU/memory resource metrics for backend API pods.",
                "Backend API pods: nominal CPU/memory. Response times and error rates unchanged "
                "from baseline.",
            ),
            Specialist.NETWORKING: _tool(
                "Check CDN/network status for static asset delivery.",
                "CDN edge cache hit rate nominal, no asset delivery failures, no infrastructure "
                "changes.",
            ),
        },
        ground_truth_specialist=Specialist.FRONTEND,
        ground_truth_root_cause=(
            "Today's frontend deploy introduced a bundle regression causing an uncaught "
            "JavaScript exception specifically on Safari, which prevents the dashboard from "
            "rendering."
        ),
        reference_remediation=(
            "Roll back the frontend deploy immediately, and add a cross-browser test matrix "
            "(including Safari) to CI to catch browser-specific regressions before release."
        ),
        cross_cutting=False,
    ),
    Incident(
        id="inc-05",
        title="background worker queue backlog growing, workers restarting",
        alert=(
            "PagerDuty ALERT: background job queue backlog growing unbounded over the last "
            "hour. Worker pods have restarted 14 times in that window."
        ),
        tools={
            Specialist.PERFORMANCE: _tool(
                "Check CPU/memory resource metrics and restart history for worker pods.",
                "Worker pod memory usage climbs steadily from ~200MB to the 2GB limit over "
                "roughly 20 minutes each cycle, then the pod is OOM-killed and restarts, "
                "repeating. CPU usage is normal throughout. Pattern began after last week's "
                "release added a new job type ('bulk-export') to this worker pool.",
            ),
            Specialist.DATABASE: _tool(
                "Check database connection pool and query performance from worker processes.",
                "Worker database connections and query latency: nominal, no pool exhaustion.",
            ),
            Specialist.NETWORKING: _tool(
                "Check network/infrastructure status for the worker node pool.",
                "No packet loss, no node-level infrastructure issues, no recent scaling events.",
            ),
        },
        ground_truth_specialist=Specialist.PERFORMANCE,
        ground_truth_root_cause=(
            "A memory leak in the new 'bulk-export' job handler (added last week) causes "
            "steadily climbing memory usage until each worker pod is OOM-killed, producing a "
            "repeating crash loop that can't drain the queue fast enough."
        ),
        reference_remediation=(
            "Patch the leaking handler in the bulk-export job path, cap per-job memory with "
            "alerts, and add backpressure so the queue sheds load instead of growing unbounded "
            "while the fix ships."
        ),
        cross_cutting=False,
    ),
    Incident(
        id="inc-06",
        title="security scanner flags publicly readable storage bucket",
        alert=(
            "Automated security scanner ALERT: a customer-data storage bucket appears to be "
            "publicly readable. No user-facing symptoms reported yet."
        ),
        tools={
            Specialist.SECURITY: _tool(
                "Check bucket access policy and access logs.",
                "The 'customer-exports' bucket's access policy allows anonymous public read "
                "access, introduced by a policy change 9 days ago. Access logs show anonymous "
                "read requests from external IP addresses over the past week, consistent with "
                "the bucket being crawled by public bucket-scanning tools.",
            ),
            Specialist.PERFORMANCE: _tool(
                "Check request volume and latency for the storage service.",
                "Storage service request volume and latency: nominal, no unusual load.",
            ),
            Specialist.NETWORKING: _tool(
                "Check network/infrastructure status for the storage service.",
                "No infrastructure changes, no networking anomalies.",
            ),
        },
        ground_truth_specialist=Specialist.SECURITY,
        ground_truth_root_cause=(
            "A misconfigured bucket access policy change 9 days ago exposed a customer-data "
            "bucket to anonymous public reads, and it has since been accessed by external "
            "parties."
        ),
        reference_remediation=(
            "Lock the bucket policy down to private access immediately, audit exactly what was "
            "read and by whom, rotate any credentials that may have been exposed, and notify "
            "compliance/legal per breach policy."
        ),
        cross_cutting=False,
    ),
    Incident(
        id="inc-07",
        title="replica lag alarms with stale reads across regions",
        alert=(
            "PagerDuty ALERT: read-replica lag alarms firing; application reads from the "
            "replica occasionally return stale data."
        ),
        tools={
            Specialist.DATABASE: _tool(
                "Check replication lag, replica health, and query load.",
                "Replica lag climbing steadily over the last hour (currently 45s, alarm "
                "threshold 5s). Replica CPU and query load are normal -- the replica itself is "
                "healthy and processing the replication stream without difficulty.",
            ),
            Specialist.NETWORKING: _tool(
                "Check cross-region network link status.",
                "The VPC peering link between the primary region and the replica region shows "
                "increased latency (180ms, up from a baseline of 12ms) and intermittent packet "
                "drops beginning roughly 70 minutes ago, correlated with a reported provider-side "
                "network maintenance window in the primary region.",
            ),
            Specialist.PERFORMANCE: _tool(
                "Check CPU/memory resource metrics for the application tier.",
                "Application tier CPU/memory: nominal, no resource pressure.",
            ),
        },
        ground_truth_specialist=Specialist.NETWORKING,
        ground_truth_root_cause=(
            "Degradation on the cross-region VPC peering link (coinciding with a provider "
            "network maintenance window) is delaying the replication stream, not a problem "
            "with the replica or database itself."
        ),
        reference_remediation=(
            "Engage the network provider about the peering link degradation, temporarily route "
            "replication over a backup path if available, and widen replica-lag alert "
            "thresholds until the network issue is resolved."
        ),
        cross_cutting=True,
    ),
    Incident(
        id="inc-08",
        title="unusual slow queries against customer table from reporting role",
        alert=(
            "Database monitoring ALERT: unusual slow queries observed against the customer "
            "table, originating from an application role that normally only reads the orders "
            "table."
        ),
        tools={
            Specialist.DATABASE: _tool(
                "Check query performance, query patterns, and role permissions.",
                "The queries are slow due to a full table scan (no supporting index for this "
                "query shape) against the customer table. Query patterns show systematic "
                "column-by-column probing consistent with reconnaissance, not a normal "
                "application access pattern.",
            ),
            Specialist.SECURITY: _tool(
                "Check WAF logs and role/permission audit trail for anomalous access.",
                "The 'reporting' application role, normally scoped to read-only access on the "
                "orders table, was granted broader customer-table read access in a "
                "configuration change 3 days ago that was not part of a reviewed change "
                "request. The query patterns match automated SQL injection reconnaissance "
                "probing for exploitable columns.",
            ),
            Specialist.PERFORMANCE: _tool(
                "Check CPU/memory resource metrics for the database tier.",
                "Database tier CPU elevated moderately (~55%, baseline ~35%) consistent with "
                "the slow queries, but not at capacity limits.",
            ),
        },
        ground_truth_specialist=Specialist.SECURITY,
        ground_truth_root_cause=(
            "An unreviewed permission change over-privileged the reporting role, and the "
            "resulting access is now being probed via SQL injection reconnaissance patterns "
            "against the customer table."
        ),
        reference_remediation=(
            "Revert the reporting role's permissions to least privilege, block the source of "
            "the probing requests, patch the vulnerable query path with parameterization, and "
            "audit who made the unreviewed permission change and why."
        ),
        cross_cutting=True,
    ),
]


def get_incident(incident_id: str) -> Incident:
    for incident in INCIDENTS:
        if incident.id == incident_id:
            return incident
    raise KeyError(f"Unknown incident id: {incident_id}")
