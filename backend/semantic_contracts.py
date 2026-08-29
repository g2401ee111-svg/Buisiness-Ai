r"""
semantic_contracts.py
Governed semantic metadata layer for the AI-Powered Business Decision Engine.

KPI_CONTRACTS is the canonical registry of every metric served by the system.
Each entry describes:
  - source_table   : the primary SQLite table (and any secondary sources)
  - grain          : the temporal granularity of one row in the source table
  - cadence        : how frequently the metric is evaluated / alerted on
  - threshold      : the anomaly-detection trigger (% or absolute $)
  - threshold_type : 'pct' | 'absolute_usd'
  - direction      : 'bidirectional' | 'increase_only' | 'decrease_only'
  - owner          : team accountable for the metric definition and data quality
  - lineage        : upstream data sources (raw -> curated)
  - access         : audience allowed to view this metric
  - description    : human-readable definition used in LLM prompts and UI tooltips

The analytics and AI layers MUST resolve metric metadata through this registry
rather than hard-coding thresholds or table names inline.  This prevents silent
semantic drift when a source table, threshold, or owner changes.
"""

# ---------------------------------------------------------------------------
# Threshold types
# ---------------------------------------------------------------------------
PCT = "pct"               # relative percent change
ABSOLUTE_USD = "absolute_usd"   # absolute dollar amount

# ---------------------------------------------------------------------------
# Cadences
# ---------------------------------------------------------------------------
HOURLY  = "hourly"
DAILY   = "daily"
WEEKLY  = "weekly"

# ---------------------------------------------------------------------------
# Access levels (ordered from most to least permissive)
# ---------------------------------------------------------------------------
ALL         = "all"
ENGINEERING = "engineering"
EXECUTIVE   = "executive"

# ---------------------------------------------------------------------------
# KPI contract registry
# ---------------------------------------------------------------------------

KPI_CONTRACTS: dict[str, dict] = {

    # ------------------------------------------------------------------
    # Revenue
    # Source: Financial / transactions table (daily grain, weekly cadence)
    # ------------------------------------------------------------------
    "revenue": {
        "label": "Total Revenue",
        "source_table": "transactions",
        "secondary_sources": [],
        "grain": DAILY,
        "cadence": WEEKLY,
        # Flag any week-over-week move beyond ±3 % as an anomaly.
        "threshold": 3.0,
        "threshold_type": PCT,
        "direction": "bidirectional",
        "owner": "Finance",
        "lineage": "raw_stripe_events -> transactions",
        "access": EXECUTIVE,
        "description": (
            "Aggregate net revenue across all customers and products for the "
            "observation period. Computed as SUM(transactions.amount) filtered "
            "by period. A week-over-week change beyond ±3 % triggers an anomaly "
            "alert reviewed by Finance."
        ),
        "sql_template": (
            "SELECT period, SUM(amount) AS total "
            "FROM transactions "
            "GROUP BY period"
        ),
        "minimum_history_days": 14,
    },

    # ------------------------------------------------------------------
    # Payment Error Rate
    # Source: Telemetry/Infrastructure / gateway_latency_logs (hourly grain)
    # ------------------------------------------------------------------
    "payment_error_rate": {
        "label": "Payment Error Rate",
        "source_table": "gateway_latency_logs",
        "secondary_sources": [],
        "grain": HOURLY,
        "cadence": HOURLY,
        # Alert when the rolling error rate exceeds 2.5 % of requests.
        "threshold": 2.5,
        "threshold_type": PCT,
        "direction": "increase_only",
        "owner": "Eng/Infra",
        "lineage": "edge_gateway_logs -> gateway_latency_logs",
        "access": ENGINEERING,
        "description": (
            "Fraction of payment-gateway requests that result in an error "
            "within any given hour, expressed as a percentage "
            "(error_count / total_requests * 100). Baseline is ~1.2 %; "
            "anything above 2.5 % triggers an immediate Eng/Infra alert."
        ),
        "sql_template": (
            "SELECT hour_timestamp, service, "
            "CAST(error_count AS REAL) / total_requests * 100 AS error_rate_pct, "
            "p99_latency_ms "
            "FROM gateway_latency_logs "
            "WHERE service = 'payment-api' "
            "ORDER BY hour_timestamp"
        ),
        "minimum_history_days": 2,
    },

    # ------------------------------------------------------------------
    # At-Risk Revenue
    # Source: transactions + support_tickets + churn model output
    # Cadence: Weekly
    # ------------------------------------------------------------------
    "at_risk_revenue": {
        "label": "At-Risk Revenue",
        "source_table": "transactions",
        "secondary_sources": ["support_tickets"],
        "grain": DAILY,
        "cadence": WEEKLY,
        # Alert when at-risk revenue exceeds $10 000 in the observation window.
        "threshold": 10_000,
        "threshold_type": ABSOLUTE_USD,
        "direction": "increase_only",
        "owner": "RevOps",
        "lineage": "transactions + support_tickets",
        "access": EXECUTIVE,
        "description": (
            "Current-period revenue held by customers who have experienced at "
            "least one payment failure (failed=1) or filed a churn-signal "
            "support ticket (categories: payment_failure, pricing). This is "
            "the revenue we may lose if the root cause is not resolved. "
            "A weekly value above $10 000 requires a RevOps review."
        ),
        "sql_template": (
            "SELECT SUM(t.amount) AS at_risk_revenue "
            "FROM transactions t "
            "WHERE t.period = 'current' "
            "AND t.customer_id IN ("
            "  SELECT DISTINCT customer_id FROM transactions WHERE failed = 1 "
            "  UNION "
            "  SELECT DISTINCT customer_id FROM support_tickets "
            "  WHERE category IN ('payment_failure', 'pricing')"
            ")"
        ),
        "minimum_history_days": 7,
    },

    # ------------------------------------------------------------------
    # DAU / MAU Engagement
    # Source: Operational / daily_active_users (daily grain)
    # ------------------------------------------------------------------
    "dau_engagement": {
        "label": "DAU / MAU Engagement",
        "source_table": "daily_active_users",
        "secondary_sources": [],
        "grain": DAILY,
        "cadence": DAILY,
        # Alert when day-over-day active-minutes drop exceeds -10 %.
        "threshold": -10.0,
        "threshold_type": PCT,
        "direction": "decrease_only",
        "owner": "Product",
        "lineage": "client_telemetry -> daily_active_users",
        "access": ALL,
        "description": (
            "Daily active engagement measured as average active_minutes per "
            "user per day, and session_count per user per day, sourced from "
            "the daily_active_users table populated by client telemetry. "
            "A day-over-day drop of more than 10 % triggers a Product alert. "
            "The metric is available to all internal audiences."
        ),
        "sql_template": (
            "SELECT date, "
            "AVG(active_minutes) AS avg_active_minutes, "
            "AVG(session_count)  AS avg_sessions "
            "FROM daily_active_users "
            "GROUP BY date "
            "ORDER BY date"
        ),
        "minimum_history_days": 7,
    },
}


# ---------------------------------------------------------------------------
# Helper utilities consumed by analytics.py and ai.py
# ---------------------------------------------------------------------------

def get_contract(kpi_id: str) -> dict:
    """Return the contract for *kpi_id*, raising KeyError if unknown.

    Usage::

        contract = get_contract("revenue")
        threshold = contract["threshold"]   # 3.0 (%)
    """
    if kpi_id not in KPI_CONTRACTS:
        raise KeyError(
            f"Unknown KPI '{kpi_id}'. "
            f"Valid IDs: {sorted(KPI_CONTRACTS.keys())}"
        )
    return KPI_CONTRACTS[kpi_id]


def list_contracts(access_level: str | None = None) -> list[dict]:
    """Return all contracts, optionally filtered by *access_level*.

    Access hierarchy (least → most restrictive):
        all < engineering < executive

    Passing access_level='engineering' returns contracts whose access is
    'engineering' OR 'all'.
    """
    ACCESS_ORDER = {ALL: 0, ENGINEERING: 1, EXECUTIVE: 2}

    contracts = []
    for kpi_id, contract in KPI_CONTRACTS.items():
        entry = dict(contract)
        entry["kpi_id"] = kpi_id
        if access_level is None:
            contracts.append(entry)
        else:
            level_required = ACCESS_ORDER.get(contract["access"], 99)
            level_caller   = ACCESS_ORDER.get(access_level, 0)
            # Caller can see this KPI if their level is >= the required level
            # OR if the KPI is public ('all').
            if contract["access"] == ALL or level_caller >= level_required:
                contracts.append(entry)
    return contracts


def threshold_breached(kpi_id: str, observed_value: float) -> bool:
    """Return True if *observed_value* breaches the KPI threshold.

    For PCT thresholds the value is treated as a signed percentage change.
    For ABSOLUTE_USD thresholds the value is treated as a raw dollar amount.
    Direction is respected:
      - 'bidirectional': breach if abs(value) >= threshold
      - 'increase_only': breach if value >= threshold
      - 'decrease_only': breach if value <= -abs(threshold)
    """
    contract  = get_contract(kpi_id)
    threshold = contract["threshold"]
    direction = contract["direction"]

    if direction == "bidirectional":
        return abs(observed_value) >= threshold
    elif direction == "increase_only":
        return observed_value >= threshold
    else:  # decrease_only
        return observed_value <= -abs(threshold)
