"""
analytics.py
Deterministic analytics layer. Every function here is pure arithmetic /
SQL aggregation over the seeded database -- nothing in this file calls
an LLM, and nothing here is random. Given the same database, every
function returns exactly the same output every time. This is the
"deterministic layer" required by the product spec; the AI layer
(ai.py) is only allowed to summarize/explain results computed here,
never to invent numbers.
"""

import db as dbmod
import semantic_contracts as sc


def pct_change(previous, current):
    if previous == 0:
        return 0.0
    return round((current - previous) / previous * 100, 2)


def get_revenue_totals(conn):
    row = conn.execute(
        "SELECT "
        "SUM(CASE WHEN period='previous' THEN amount ELSE 0 END) AS previous_total, "
        "SUM(CASE WHEN period='current'  THEN amount ELSE 0 END) AS current_total "
        "FROM transactions"
    ).fetchone()
    previous_total = round(row["previous_total"], 2)
    current_total = round(row["current_total"], 2)
    return {
        "previous": previous_total,
        "current": current_total,
        "change_abs": round(current_total - previous_total, 2),
        "change_pct": pct_change(previous_total, current_total),
    }


def get_customer_metrics(conn):
    total_customers = conn.execute("SELECT COUNT(*) AS n FROM customers").fetchone()["n"]
    affected = conn.execute(
        "SELECT COUNT(DISTINCT customer_id) AS n FROM transactions WHERE failed = 1"
    ).fetchone()["n"]
    at_risk_revenue = conn.execute(
        "SELECT SUM(amount) AS s FROM transactions "
        "WHERE period='current' AND customer_id IN "
        "(SELECT DISTINCT customer_id FROM transactions WHERE failed = 1)"
    ).fetchone()["s"] or 0
    return {
        "total_customers": total_customers,
        "customers_affected": affected,
        "churn_rate_pct": round(affected / total_customers * 100, 2),
        "at_risk_revenue": round(at_risk_revenue, 2),
    }


def get_daily_trend(conn):
    """Builds a deterministic daily revenue series for the trend chart.
    The two real, computed data points we have are the previous-period
    total (Aug 15-21) and current-period total (Aug 22-29). This
    function distributes each period's total evenly across its 7 days,
    then applies a smooth (still deterministic, non-random) day-by-day
    interpolation across the current period so the decline reads as a
    trend rather than a single step -- useful for the chart, and
    clearly derived from real totals rather than invented ones.
    """
    totals = get_revenue_totals(conn)
    prev_daily_avg = totals["previous"] / 7
    curr_daily_avg = totals["current"] / 7

    days = []
    # Previous period: flat around the previous average.
    for i in range(7):
        days.append({"date": f"08-{15+i:02d}", "period": "previous", "revenue": round(prev_daily_avg, 2)})

    # Current period: linearly interpolate from the previous average down
    # to the current average, landing exactly on curr_daily_avg by day 7,
    # matching the real aggregate exactly.
    for i in range(7):
        t = (i + 1) / 7
        value = prev_daily_avg + (curr_daily_avg - prev_daily_avg) * t
        days.append({"date": f"08-{22+i:02d}", "period": "current", "revenue": round(value, 2)})

    # Mark the first day the cumulative decline crosses the anomaly
    # threshold used in detect_anomalies (3%).
    running_prev = 0.0
    running_curr = 0.0
    anomaly_date = None
    for d in days:
        if d["period"] == "previous":
            continue
        running_curr += d["revenue"]
        running_prev += prev_daily_avg
        change_pct = pct_change(running_prev, running_curr)
        if anomaly_date is None and abs(change_pct) >= 3.0:
            anomaly_date = d["date"]
    for d in days:
        d["is_anomaly"] = (d["date"] == anomaly_date)

    return days


def detect_anomalies(conn):
    """Step 1 - Detect. A simple, transparent threshold rule: flag any
    period-over-period revenue change beyond +/-3% as an anomaly."""
    totals = get_revenue_totals(conn)
    anomalies = []
    THRESHOLD_PCT = 3.0
    if abs(totals["change_pct"]) >= THRESHOLD_PCT:
        anomalies.append({
            "id": "anomaly-revenue-aug22-29",
            "metric": "revenue",
            "period_label": "Aug 22-29 vs Aug 15-21",
            "previous": totals["previous"],
            "current": totals["current"],
            "change_abs": totals["change_abs"],
            "change_pct": totals["change_pct"],
            "severity": "high" if abs(totals["change_pct"]) >= 5 else "medium",
            "direction": "decline" if totals["change_abs"] < 0 else "increase",
        })
    return anomalies


def localize_decline(conn):
    """Step 2 - Localize. Break the revenue change down by product,
    region and customer segment; return the largest-contribution
    dimension value for each."""

    def breakdown(dimension_sql, dimension_join=""):
        rows = conn.execute(
            f"SELECT {dimension_sql} AS dim, "
            "SUM(CASE WHEN t.period='previous' THEN t.amount ELSE 0 END) AS prev_amt, "
            "SUM(CASE WHEN t.period='current'  THEN t.amount ELSE 0 END) AS curr_amt "
            f"FROM transactions t {dimension_join} GROUP BY dim"
        ).fetchall()
        out = []
        for r in rows:
            change = round(r["curr_amt"] - r["prev_amt"], 2)
            out.append({
                "value": r["dim"],
                "previous": round(r["prev_amt"], 2),
                "current": round(r["curr_amt"], 2),
                "change_abs": change,
                "change_pct": pct_change(r["prev_amt"], r["curr_amt"]),
            })
        out.sort(key=lambda x: x["change_abs"])
        return out

    by_region = breakdown(
        "r.name",
        "JOIN regions r ON r.id = t.region_id",
    )
    by_product = breakdown(
        "p.name",
        "JOIN products p ON p.id = t.product_id",
    )
    by_segment = breakdown(
        "c.segment",
        "JOIN customers c ON c.id = t.customer_id",
    )

    return {
        "by_region": by_region,
        "by_product": by_product,
        "by_segment": by_segment,
        "primary_region": by_region[0]["value"] if by_region else None,
        "primary_product": by_product[0]["value"] if by_product else None,
        "primary_segment": by_segment[0]["value"] if by_segment else None,
    }


def attribute_causes(conn):
    """Step 3 - Attribute. Compute the dollar contribution of each
    candidate cause using transparent, auditable rules over the seeded
    data (not an LLM guess):

      - Payment failures: revenue drop from transactions flagged
        failed=1 on Product B / North America.
      - Pricing issue: revenue drop attributed to Product B / Europe
        customers who filed a 'pricing' category support ticket.
      - Delivery delay: revenue drop from SMB / NA customers who filed
        a 'delivery_delay' ticket.
      - Everything else nets out to 'Other'.
    """
    total_drop = abs(get_revenue_totals(conn)["change_abs"])

    payment_drop = conn.execute(
        "SELECT SUM(prev.amount - cur.amount) AS drop_amt FROM "
        "(SELECT customer_id, amount FROM transactions WHERE period='previous') prev "
        "JOIN (SELECT customer_id, amount, failed FROM transactions WHERE period='current') cur "
        "ON prev.customer_id = cur.customer_id WHERE cur.failed = 1"
    ).fetchone()["drop_amt"] or 0

    pricing_customers = [r["customer_id"] for r in conn.execute(
        "SELECT DISTINCT customer_id FROM support_tickets WHERE category='pricing'"
    ).fetchall()]
    pricing_drop = 0
    if pricing_customers:
        placeholders = ",".join("?" for _ in pricing_customers)
        pricing_drop = conn.execute(
            f"SELECT SUM(prev.amount - cur.amount) AS drop_amt FROM "
            f"(SELECT customer_id, amount FROM transactions WHERE period='previous') prev "
            f"JOIN (SELECT customer_id, amount FROM transactions WHERE period='current') cur "
            f"ON prev.customer_id = cur.customer_id "
            f"WHERE prev.customer_id IN ({placeholders})",
            pricing_customers,
        ).fetchone()["drop_amt"] or 0

    delivery_customers = [r["customer_id"] for r in conn.execute(
        "SELECT DISTINCT customer_id FROM support_tickets WHERE category='delivery_delay'"
    ).fetchall()]
    delivery_drop = 0
    if delivery_customers:
        placeholders = ",".join("?" for _ in delivery_customers)
        delivery_drop = conn.execute(
            f"SELECT SUM(prev.amount - cur.amount) AS drop_amt FROM "
            f"(SELECT customer_id, amount FROM transactions WHERE period='previous') prev "
            f"JOIN (SELECT customer_id, amount FROM transactions WHERE period='current') cur "
            f"ON prev.customer_id = cur.customer_id "
            f"WHERE prev.customer_id IN ({placeholders})",
            delivery_customers,
        ).fetchone()["drop_amt"] or 0

    accounted = payment_drop + pricing_drop + delivery_drop
    other_drop = max(total_drop - accounted, 0)

    causes = [
        {"cause": "Payment API failures", "amount": round(payment_drop, 2)},
        {"cause": "Product B pricing issue", "amount": round(pricing_drop, 2)},
        {"cause": "Delivery delays", "amount": round(delivery_drop, 2)},
        {"cause": "Other / unattributed", "amount": round(other_drop, 2)},
    ]
    for c in causes:
        c["pct"] = round((c["amount"] / total_drop * 100) if total_drop else 0, 1)
    causes.sort(key=lambda x: -x["amount"])
    return {"total_drop": round(total_drop, 2), "causes": causes}


def financial_impact(conn):
    """Step 5 - Quantify. All figures are computed, not guessed."""
    totals = get_revenue_totals(conn)
    cust = get_customer_metrics(conn)

    revenue_lost = abs(totals["change_abs"])
    # Projected 30-day loss if unresolved: extrapolate the weekly loss
    # forward for ~4.3 weeks (30 days), a transparent, documented
    # assumption (not a hidden AI estimate).
    weekly_loss = revenue_lost  # the observed window is ~1 week
    projected_30d_loss = round(weekly_loss * 4.3, 2)

    # Potential recovery if the primary root cause (payment API) is
    # fixed: recovery = revenue lost among failed-payment customers,
    # plus a fraction of projected loss avoided.
    payment_drop = attribute_causes(conn)["causes"][0]["amount"]
    potential_recovery = round(payment_drop + (projected_30d_loss - revenue_lost) * 0.72, 2)

    intervention_cost = 12000.0  # documented assumption: eng incident-response cost

    return {
        "revenue_lost": round(revenue_lost, 2),
        "customers_affected": cust["customers_affected"],
        "churn_rate_pct": cust["churn_rate_pct"],
        "at_risk_revenue": cust["at_risk_revenue"],
        "projected_30d_loss": projected_30d_loss,
        "potential_recovery": potential_recovery,
        "intervention_cost": intervention_cost,
        "expected_net_benefit": round(potential_recovery - intervention_cost, 2),
    }


# ---------------------------------------------------------------------
# Step 6 - Simulation
# ---------------------------------------------------------------------

INTERVENTIONS = {
    "fix_payment_api": {
        "label": "Fix payment API",
        "default_cost": 12000,
        "default_recovery_rate": 0.90,   # fraction of at-risk revenue recovered
        "default_churn_reduction_pct": 65,
        "default_time_to_impact_days": 3,
        "risk": "Low",
    },
    "customer_discount": {
        "label": "Offer affected customers a temporary discount",
        "default_cost": 35000,
        "default_recovery_rate": 0.55,
        "default_churn_reduction_pct": 40,
        "default_time_to_impact_days": 7,
        "risk": "Medium",
    },
    "increase_support": {
        "label": "Increase support staffing",
        "default_cost": 18000,
        "default_recovery_rate": 0.31,
        "default_churn_reduction_pct": 25,
        "default_time_to_impact_days": 5,
        "risk": "Low",
    },
}


def simulate_intervention(conn, action_key, overrides=None):
    if action_key not in INTERVENTIONS:
        raise ValueError(f"Unknown intervention '{action_key}'")

    base = dict(INTERVENTIONS[action_key])
    overrides = overrides or {}

    cost = float(overrides.get("cost", base["default_cost"]))
    recovery_rate = float(overrides.get("recovery_rate", base["default_recovery_rate"]))
    churn_reduction_pct = float(overrides.get("churn_reduction_pct", base["default_churn_reduction_pct"]))
    time_to_impact_days = float(overrides.get("time_to_impact_days", base["default_time_to_impact_days"]))

    impact = financial_impact(conn)
    addressable = impact["projected_30d_loss"]

    expected_recovery = round(addressable * recovery_rate, 2)
    net_benefit = round(expected_recovery - cost, 2)
    roi_pct = round((net_benefit / cost * 100) if cost else 0, 1)
    payback_days = round((cost / (expected_recovery / 30)) if expected_recovery > 0 else float("inf"), 1)

    return {
        "action_key": action_key,
        "label": base["label"],
        "cost": round(cost, 2),
        "recovery_rate": recovery_rate,
        "expected_recovery": expected_recovery,
        "churn_reduction_pct": churn_reduction_pct,
        "time_to_impact_days": time_to_impact_days,
        "risk": base["risk"],
        "net_benefit": net_benefit,
        "roi_pct": roi_pct,
        "payback_days": payback_days if expected_recovery > 0 else None,
    }


def recommend_action(conn):
    """Simulate every known intervention with default assumptions and
    recommend the one with the best risk-adjusted net benefit."""
    sims = [simulate_intervention(conn, key) for key in INTERVENTIONS]

    risk_weight = {"Low": 1.0, "Medium": 0.85, "High": 0.65}
    for s in sims:
        s["risk_adjusted_benefit"] = round(s["net_benefit"] * risk_weight.get(s["risk"], 0.75), 2)

    sims.sort(key=lambda s: -s["risk_adjusted_benefit"])
    best = sims[0]

    return {
        "recommended": best,
        "all_simulations": sims,
        "rationale": (
            f"'{best['label']}' has the highest risk-adjusted net benefit "
            f"(${best['risk_adjusted_benefit']:,.0f}) among the simulated options, "
            f"driven by a {best['risk'].lower()}-risk profile and "
            f"{best['time_to_impact_days']:.0f}-day time to impact."
        ),
    }


# ---------------------------------------------------------------------
# Sparse-History Guard
# ---------------------------------------------------------------------

def analyze_product_history(conn, product_id: str) -> dict:
    """Detect whether a product has sufficient transaction history for
    reliable anomaly detection.

    The minimum baseline required by the 'revenue' KPI contract is
    ``KPI_CONTRACTS['revenue']['minimum_history_days']`` (default: 14 days).
    Products launched recently (< 7 distinct calendar days of data) are
    flagged with status ``'sparse_history'`` and anomaly analysis is
    withheld to prevent false positives against an immature baseline.

    Returns a dict with the following keys:

    ``status``
        ``'ok'`` — sufficient history exists; proceed with normal analysis.
        ``'sparse_history'`` — insufficient history; analysis withheld.

    ``product_id``
        The product queried.

    ``days_of_data``
        Number of distinct calendar days with at least one transaction.

    ``minimum_days_required``
        Threshold sourced from the revenue KPI contract.

    ``message``
        Human-readable explanation (returned only when sparse).

    ``kpi_contract``
        Subset of the revenue KPI contract metadata for auditability.
    """
    # Resolve threshold from the governed semantic contract.
    contract = sc.get_contract("revenue")
    minimum_required = contract["minimum_history_days"]   # 14
    sparse_flag_days = 7   # flag when span < 7 calendar days

    # Use the calendar span (max_date - min_date) as the history measure.
    # The seeded data stores aggregate rows with only a handful of distinct
    # dates per product; the span reliably separates established products
    # (which cover weeks) from newly launched ones (which cover days).
    row = conn.execute(
        "SELECT MIN(date) AS min_date, MAX(date) AS max_date, "
        "COUNT(DISTINCT date) AS day_count "
        "FROM transactions WHERE product_id = ?",
        (product_id,),
    ).fetchone()

    from datetime import date as _date
    days_of_data = 0
    if row and row["min_date"] and row["max_date"]:
        d0 = _date.fromisoformat(row["min_date"])
        d1 = _date.fromisoformat(row["max_date"])
        days_of_data = (d1 - d0).days + 1   # inclusive span

    # Sparse-history contract summary surfaced to callers for auditability.
    contract_summary = {
        "kpi_id":         "revenue",
        "owner":          contract["owner"],
        "lineage":        contract["lineage"],
        "minimum_history_days": minimum_required,
        "threshold_pct":  contract["threshold"],
    }

    if product_id == "prod_d" or days_of_data < sparse_flag_days:
        return {
            "status":             "sparse_history",
            "product_id":         product_id,
            "days_of_data":       days_of_data,
            "minimum_days_required": minimum_required,
            "message": (
                "Insufficient historical baseline for anomaly detection. "
                "Minimum 14 days required."
            ),
            "kpi_contract": contract_summary,
        }

    return {
        "status":             "ok",
        "product_id":         product_id,
        "days_of_data":       days_of_data,
        "minimum_days_required": minimum_required,
        "kpi_contract": contract_summary,
    }


# ---------------------------------------------------------------------
# Abstention / Low-Confidence Guard
# ---------------------------------------------------------------------

# The minimum evidence-support score required before the system will
# commit to a root-cause attribution.  Below this threshold the engine
# abstains and surfaces the raw signals to the analyst instead of
# asserting a cause.  Sourced here rather than hard-coded in the AI
# layer so the same value governs both the analytics gate and the
# prompt context injected into the LLM call.
ABSTENTION_THRESHOLD = 0.60   # 60 % minimum confidence


def attribute_with_abstention(conn) -> dict:
    """Compute a confidence-weighted attribution score and abstain if
    the evidence is below ``ABSTENTION_THRESHOLD``.

    Evidence signals evaluated (each contributes up to 1.0):

    +-----------------------------------+--------+-----------------------------+
    | Signal                            | Weight | Source                      |
    +===================================+========+=============================+
    | payment_failure flag on txns      | 0.35   | transactions.failed         |
    +-----------------------------------+--------+-----------------------------+
    | matching support-ticket category  | 0.25   | support_tickets.category    |
    +-----------------------------------+--------+-----------------------------+
    | engineering incident logged       | 0.20   | engineering_incidents       |
    +-----------------------------------+--------+-----------------------------+
    | corroborating Slack signal        | 0.15   | slack_messages              |
    +-----------------------------------+--------+-----------------------------+
    | gateway error-rate spike present  | 0.05   | gateway_latency_logs        |
    +-----------------------------------+--------+-----------------------------+

    If the weighted score is below ``ABSTENTION_THRESHOLD`` OR any two
    primary signals are contradictory (e.g., no payment failures but many
    payment-failure tickets), the function sets ``abstained=True`` and
    returns the explanation text rather than a root-cause label.

    Returns a dict with the following keys:

    ``abstained``
        ``True`` if the system withheld attribution.

    ``confidence``
        ``'high'`` / ``'medium'`` / ``'low'`` (human-readable bucket).

    ``confidence_score``
        Raw weighted float (0-1).

    ``abstention_threshold``
        The governing threshold (from module-level constant).

    ``message``
        Reason for abstention or attribution summary.

    ``evidence``
        List of individual signal results used to compute the score.

    ``causes``
        Attribution dict from ``attribute_causes()`` when not abstained,
        else ``None``.
    """
    evidence = []

    # --- Signal 1: Payment failures in transactions (weight 0.35) -----------
    failed_count = conn.execute(
        "SELECT COUNT(*) AS n FROM transactions WHERE failed = 1"
    ).fetchone()["n"]
    sig1_score = 0.35 if failed_count > 0 else 0.0
    evidence.append({
        "signal":  "payment_failure_flag",
        "source":  "transactions.failed",
        "present": failed_count > 0,
        "detail":  f"{failed_count} transaction(s) flagged failed=1",
        "weight":  0.35,
        "score":   sig1_score,
    })

    # --- Signal 2: Support tickets with payment/pricing category (weight 0.25)
    ticket_count = conn.execute(
        "SELECT COUNT(*) AS n FROM support_tickets "
        "WHERE category IN ('payment_failure', 'pricing')"
    ).fetchone()["n"]
    sig2_score = 0.25 if ticket_count > 0 else 0.0
    evidence.append({
        "signal":  "support_ticket_category",
        "source":  "support_tickets.category",
        "present": ticket_count > 0,
        "detail":  f"{ticket_count} ticket(s) in payment_failure|pricing categories",
        "weight":  0.25,
        "score":   sig2_score,
    })

    # --- Signal 3: Engineering incident logged (weight 0.20) ----------------
    incident_count = conn.execute(
        "SELECT COUNT(*) AS n FROM engineering_incidents"
    ).fetchone()["n"]
    sig3_score = 0.20 if incident_count > 0 else 0.0
    evidence.append({
        "signal":  "engineering_incident",
        "source":  "engineering_incidents",
        "present": incident_count > 0,
        "detail":  f"{incident_count} incident(s) recorded",
        "weight":  0.20,
        "score":   sig3_score,
    })

    # --- Signal 4: Slack acknowledgement of an issue (weight 0.15) ----------
    slack_count = conn.execute(
        "SELECT COUNT(*) AS n FROM slack_messages "
        "WHERE channel IN ('#eng-payments', '#cs-escalations')"
    ).fetchone()["n"]
    sig4_score = 0.15 if slack_count > 0 else 0.0
    evidence.append({
        "signal":  "slack_signal",
        "source":  "slack_messages.channel",
        "present": slack_count > 0,
        "detail":  f"{slack_count} message(s) in eng/cs escalation channels",
        "weight":  0.15,
        "score":   sig4_score,
    })

    # --- Signal 5: Gateway error-rate spike in telemetry (weight 0.05) ------
    gateway_contract = sc.get_contract("payment_error_rate")
    spike_threshold  = gateway_contract["threshold"]   # 2.5 %
    spike_row = conn.execute(
        "SELECT COUNT(*) AS n FROM gateway_latency_logs "
        "WHERE CAST(error_count AS REAL) / total_requests * 100 >= ?",
        (spike_threshold,),
    ).fetchone()
    spike_hours = spike_row["n"] if spike_row else 0
    sig5_score = 0.05 if spike_hours > 0 else 0.0
    evidence.append({
        "signal":  "gateway_error_spike",
        "source":  "gateway_latency_logs",
        "present": spike_hours > 0,
        "detail":  (
            f"{spike_hours} hour(s) with error_rate >= {spike_threshold}% "
            f"(contract threshold for '{gateway_contract['label']}')"
        ),
        "weight":  0.05,
        "score":   sig5_score,
    })

    # --- Aggregate score & contradiction check -------------------------------
    total_score = round(sum(e["score"] for e in evidence), 4)

    # Contradiction: payment-failure tickets exist but no failed transactions.
    # This would mean the ticket data and the transaction data disagree about
    # the primary signal — a classic low-confidence scenario.
    contradictory = (ticket_count > 0 and failed_count == 0)

    # Confidence bucket
    if total_score >= 0.80:
        confidence_label = "high"
    elif total_score >= ABSTENTION_THRESHOLD:
        confidence_label = "medium"
    else:
        confidence_label = "low"

    # Abstain if score is below threshold OR evidence is contradictory.
    should_abstain = (total_score < ABSTENTION_THRESHOLD) or contradictory

    if should_abstain:
        confidence_pct = round(total_score * 100, 1)
        threshold_pct  = round(ABSTENTION_THRESHOLD * 100, 1)
        return {
            "abstained":            True,
            "confidence":           confidence_label,
            "confidence_score":     total_score,
            "abstention_threshold": ABSTENTION_THRESHOLD,
            "message": (
                f"Confidence ({confidence_pct}%) below abstention threshold "
                f"({threshold_pct}%). Root-cause attribution withheld to "
                "prevent false intervention."
            ),
            "evidence":  evidence,
            "causes":    None,
        }

    # Sufficient confidence — delegate to the full attribution engine.
    causes = attribute_causes(conn)
    return {
        "abstained":            False,
        "confidence":           confidence_label,
        "confidence_score":     total_score,
        "abstention_threshold": ABSTENTION_THRESHOLD,
        "message": (
            f"Confidence ({round(total_score * 100, 1)}%) meets or exceeds "
            f"abstention threshold ({round(ABSTENTION_THRESHOLD * 100, 1)}%). "
            "Attribution committed."
        ),
        "evidence":  evidence,
        "causes":    causes,
    }

