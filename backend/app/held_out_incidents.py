"""4 held-out incidents, designed after every current fix (confidence floor,
judge-vote count, sanity check, secondary-perspective diagnose) was already in
place -- none of these were used to tune anything. Kept separate from
`incidents.py` until a real batch run confirms they behave sanely; if they do,
they get folded into the main dataset (see README's "Real results" section for
what that run showed).
"""

from __future__ import annotations

from app.models import Incident, Specialist, ToolDefinition


def _tool(description: str, result: str) -> ToolDefinition:
    return ToolDefinition(description=description, result=result)


HELD_OUT_INCIDENTS: list[Incident] = [
    Incident(
        id="inc-09",
        title="api-gateway intermittent 502s after certificate rotation",
        alert=(
            "PagerDuty ALERT: api-gateway returning 502 Bad Gateway on ~8% of requests since "
            "03:00 UTC, following a scheduled TLS certificate rotation at 02:55 UTC."
        ),
        tools={
            Specialist.NETWORKING: _tool(
                "Check TLS certificate chain and load balancer/gateway configuration.",
                "The certificate deployed at 02:55 UTC is missing an intermediate CA in its chain. "
                "Clients/load balancers whose trust store has not yet cached the intermediate "
                "reject the handshake, producing intermittent 502s depending on client-side cache "
                "state.",
            ),
            Specialist.PERFORMANCE: _tool(
                "Check CPU/memory resource metrics for gateway pods.",
                "Gateway pod CPU/memory: nominal, no resource pressure, no restarts.",
            ),
            Specialist.DATABASE: _tool(
                "Check database connection pool and query performance.",
                "Connection pool and query latency: nominal, unaffected.",
            ),
            Specialist.SECURITY: _tool(
                "Check authentication logs and WAF logs for anomalies.",
                "No anomalous authentication attempts, no WAF block spike.",
            ),
        },
        ground_truth_specialist=Specialist.NETWORKING,
        ground_truth_root_cause=(
            "The TLS certificate rotation deployed a certificate chain missing an intermediate "
            "CA, so clients whose trust store hasn't cached the new intermediate reject the "
            "handshake intermittently, producing 502s."
        ),
        reference_remediation=(
            "Redeploy the certificate bundle with the correct intermediate CA chain, verify with "
            "an SSL chain-validation tool, and add a pre-deployment check for complete "
            "certificate chains before future rotations."
        ),
        cross_cutting=False,
    ),
    Incident(
        id="inc-10",
        title="search returns zero results for a subset of users after index migration",
        alert=(
            "Support tickets: users report search returns zero results for common queries since "
            "this morning's search-index migration; other users report search working normally."
        ),
        tools={
            Specialist.DATABASE: _tool(
                "Check search index migration status and shard/alias configuration.",
                "This morning's index migration completed for most shards, but a subset of "
                "shards still reference the old (now-decommissioned) index alias. Queries hashed "
                "to those shards return zero results even though the query itself is valid.",
            ),
            Specialist.PERFORMANCE: _tool(
                "Check CPU/memory resource metrics for the search service.",
                "Search service CPU/memory: nominal, no resource pressure.",
            ),
            Specialist.FRONTEND: _tool(
                "Check browser console errors and client-side error tracking.",
                "No client-side errors. Requests complete successfully and render an empty "
                "results state -- not a rendering bug.",
            ),
            Specialist.NETWORKING: _tool(
                "Check network/infrastructure status.",
                "No packet loss, no infrastructure changes, no anomalies.",
            ),
        },
        ground_truth_specialist=Specialist.DATABASE,
        ground_truth_root_cause=(
            "This morning's index migration only completed for a subset of shards; the "
            "remaining shards still reference the decommissioned old index alias, so queries "
            "routed to those shards return zero results."
        ),
        reference_remediation=(
            "Re-run the migration for the affected shards, verify all shard aliases point to "
            "the new index, and add a post-migration validation check that queries every shard "
            "before considering a migration complete."
        ),
        cross_cutting=False,
    ),
    Incident(
        id="inc-11",
        title="checkout abandonment spike correlated with a slow third-party shipping-rate API",
        alert=(
            "PagerDuty ALERT: checkout completion rate dropped from 92% to 61% over the last 2 "
            "hours; no error rate increase, but average checkout page load time increased from "
            "1.2s to 9.8s."
        ),
        tools={
            Specialist.NETWORKING: _tool(
                "Check outbound third-party API latency and status.",
                "Outbound calls to the third-party shipping-rate API are taking 8-9s to respond "
                "(up from a normal ~200ms). The provider's own status page reports a regional "
                "outage. No issue detected on our own network path.",
            ),
            Specialist.PERFORMANCE: _tool(
                "Check CPU/memory and thread pool metrics for checkout-service.",
                "checkout-service CPU/memory nominal, but the request thread pool is near "
                "exhaustion because requests are blocked waiting on the slow third-party call -- "
                "there is no timeout or circuit breaker configured around that call.",
            ),
            Specialist.DATABASE: _tool(
                "Check database connection pool and query performance.",
                "Connection pool and query latency: nominal.",
            ),
            Specialist.SECURITY: _tool(
                "Check authentication logs and traffic patterns for anomalies.",
                "No anomalous authentication attempts, traffic composition normal.",
            ),
        },
        ground_truth_specialist=Specialist.NETWORKING,
        ground_truth_root_cause=(
            "A regional outage at the third-party shipping-rate provider is slowing outbound "
            "calls to 8-9s; our own checkout-service has no timeout or circuit breaker around "
            "that call, so the slow dependency exhausts our thread pool and blocks checkouts."
        ),
        reference_remediation=(
            "Engage the third-party provider about their outage and temporarily fail over to a "
            "cached/default shipping estimate; add a request timeout and circuit breaker around "
            "the third-party call so a slow dependency can't exhaust our own thread pool again."
        ),
        cross_cutting=True,
    ),
    Incident(
        id="inc-12",
        title="customer analytics dashboard showing stale data",
        alert=(
            "Support tickets: multiple enterprise customers report the analytics dashboard is "
            "showing data frozen as of yesterday 18:00 UTC; no errors visible to users."
        ),
        tools={
            Specialist.PERFORMANCE: _tool(
                "Check scheduled job execution logs and batch job status.",
                "The nightly ETL aggregation job that refreshes the dashboard's summary tables "
                "failed last night due to an unhandled exception partway through the aggregation "
                "step. The job has no automatic retry and did not raise an alert on failure.",
            ),
            Specialist.DATABASE: _tool(
                "Check query performance on the dashboard's summary tables.",
                "Query latency against the summary tables is nominal -- the tables are simply "
                "stale, not slow to read.",
            ),
            Specialist.FRONTEND: _tool(
                "Check browser console errors and client-side error tracking.",
                "No client-side errors; the dashboard is rendering correctly, just displaying "
                "outdated data.",
            ),
            Specialist.NETWORKING: _tool(
                "Check network/infrastructure status.",
                "No packet loss, no infrastructure changes, no anomalies.",
            ),
        },
        ground_truth_specialist=Specialist.PERFORMANCE,
        ground_truth_root_cause=(
            "The nightly ETL aggregation job that refreshes the dashboard's summary tables "
            "failed silently last night due to an unhandled exception, and without automatic "
            "retry or alerting, the dashboard has been serving stale data ever since."
        ),
        reference_remediation=(
            "Manually re-run the failed aggregation job to refresh the summary tables, fix the "
            "unhandled exception in the ETL script, and add failure alerting plus automatic "
            "retry for the nightly job so a silent failure doesn't go unnoticed again."
        ),
        cross_cutting=False,
    ),
]
