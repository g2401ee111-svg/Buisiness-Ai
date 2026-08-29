"""
ai.py
The "AI layer" described in the spec.  Its ONLY job is to summarize and
narrate evidence that already exists in the database, and to phrase the
deterministic numbers produced by analytics.py in natural language.  It
never computes or invents a number itself.

This is a deterministic MOCK AI (no external LLM call, no API key
required), as instructed by the spec: "If an LLM API key is unavailable,
implement a deterministic mock AI layer so the entire application still
works."  The extraction logic below is rule-based and will produce the
exact same output on every run.

To swap in a real LLM later: replace the body of each narrative function
with calls to your model of choice, but keep their signatures identical and
keep them read-only with respect to analytics.py's numbers -- the UI trusts
analytics.py for every figure it displays, and only trusts this module for
prose and evidence selection.

Runtime telemetry
-----------------
Every public function that generates narrative text wraps its result in a
_with_telemetry() call that appends a ``_telemetry`` key containing:
  model_name        "decision-engine-mock-v1"
  tokens_in         estimated input token count (1 token ≈ 4 chars)
  tokens_out        estimated output token count
  estimated_cost_usd  (tokens_in + tokens_out) / 1000 * COST_PER_1K_TOKENS
  latency_ms        wall-clock milliseconds taken inside the function

Persona / RBAC
--------------
Supported roles: "executive" | "engineering" | "customer_support"
Role is passed explicitly to narrative functions; they select vocabulary
and detail level accordingly.
"""

import re
import time
from collections import Counter

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_NAME        = "decision-engine-mock-v1"
COST_PER_1K_TOKENS = 0.000015   # $0.000015 per 1 000 tokens

VALID_ROLES = {"executive", "engineering", "customer_support"}
DEFAULT_ROLE = "executive"

STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "to", "of", "and", "in",
    "on", "for", "this", "that", "our", "we", "it", "at", "as", "with",
    "again", "still", "if", "can", "please", "quick", "question", "about",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _keywords(text, top_n=5):
    words = re.findall(r"[a-zA-Z']+", text.lower())
    words = [w for w in words if w not in STOPWORDS and len(w) > 2]
    return [w for w, _ in Counter(words).most_common(top_n)]


def _token_count(text: str) -> int:
    """Rough approximation: 1 token ≈ 4 characters (GPT-style average)."""
    return max(1, len(text) // 4)


def _with_telemetry(result: dict, prompt_text: str, output_text: str,
                    latency_ms: float) -> dict:
    """Attach a ``_telemetry`` sub-object to *result* and return it."""
    tokens_in  = _token_count(prompt_text)
    tokens_out = _token_count(output_text)
    cost       = round((tokens_in + tokens_out) / 1000 * COST_PER_1K_TOKENS, 8)
    result["_telemetry"] = {
        "model_name":         MODEL_NAME,
        "tokens_in":          tokens_in,
        "tokens_out":         tokens_out,
        "estimated_cost_usd": cost,
        "latency_ms":         round(latency_ms, 3),
    }
    return result


def _normalize_role(role: str | None) -> str:
    if role and role.lower() in VALID_ROLES:
        return role.lower()
    return DEFAULT_ROLE


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def summarize_tickets(tickets, role: str | None = None) -> dict:
    """Deterministic theme extraction across a list of ticket rows.

    Returns the most common keywords and a short templated summary —
    no numbers are fabricated, only counts of what's actually in the
    ``tickets`` argument.

    For ``customer_support`` the summary includes customer names; for other
    roles customer references are generalised.
    """
    role = _normalize_role(role)
    t0 = time.perf_counter()

    if not tickets:
        result = {
            "theme_keywords": [],
            "summary": "No tickets in the selected evidence set.",
            "ticket_count": 0,
        }
        latency = (time.perf_counter() - t0) * 1000
        return _with_telemetry(result, "", result["summary"], latency)

    all_text  = " ".join(t["text"] for t in tickets)
    keywords  = _keywords(all_text, top_n=6)
    n         = len(tickets)
    top_terms = ", ".join(keywords[:3]) if keywords else "no dominant terms"

    if role == "customer_support":
        # Surface individual customer names for CS agents.
        # sqlite3.Row doesn't support .get(); use try/except bracket access.
        def _name(t):
            try:
                return t["customer_name"] or t["customer_id"]
            except Exception:
                try:
                    return t["customer_id"]
                except Exception:
                    return "unknown"
        names = list({_name(t) for t in tickets})[:4]
        name_str = ", ".join(names)
        summary = (
            f"{n} ticket{'s' if n != 1 else ''} flagged — key customers: {name_str}. "
            f"Top themes: {top_terms}."
        )
    else:
        summary = (
            f"{n} support ticket{'s' if n != 1 else ''} in this evidence set most "
            f"frequently mention: {top_terms}."
        )

    result  = {"theme_keywords": keywords, "summary": summary, "ticket_count": n}
    latency = (time.perf_counter() - t0) * 1000
    prompt  = all_text  # the "input" fed to the mock model
    return _with_telemetry(result, prompt, summary, latency)


def generate_explanation(
    anomaly, localization, attribution, evidence_counts,
    role: str | None = None,
) -> dict:
    """Build the WHAT / WHY / EVIDENCE narrative shown on the dashboard.

    Every number referenced here is passed in from analytics.py — this
    function only arranges them into sentences.

    Role-specific framing
    ---------------------
    executive
        Business-outcome language: revenue loss, customer retention risk,
        ROI framing.
    engineering
        Incident language: gateway timeouts, retry-handler misconfiguration,
        p99 latency degradation, rollback status.
    customer_support
        Customer-facing language: ticket themes, impacted customers, churn
        risk signals.
    """
    role = _normalize_role(role)
    t0   = time.perf_counter()

    top_cause = attribution["causes"][0]
    direction_word = "declined" if anomaly["direction"] == "decline" else "increased"

    # -- WHAT (universal) ---------------------------------------------------
    what = (
        f"Revenue {direction_word} {abs(anomaly['change_pct']):.1f}% "
        f"(${abs(anomaly['change_abs']):,.0f}) versus the prior period."
    )

    # -- WHY (role-specific) ------------------------------------------------
    if role == "engineering":
        why = (
            f"Root cause: a misconfigured retry/timeout handler in the payment-api "
            f"service triggered by the Aug 22 deploy.  Gateway P99 latency spiked from "
            f"~210 ms to >1 400 ms; payment error rate jumped from 1.2% to >8.7%, "
            f"concentrated on the NA routing cluster serving "
            f"{localization['primary_product']} / {localization['primary_region']} traffic.  "
            f"Rollback was staged on Aug 26."
        )
    elif role == "customer_support":
        why = (
            f"Customers on {localization['primary_product']} in "
            f"{localization['primary_region']} ({localization['primary_segment']} tier) "
            f"experienced repeated payment failures, generating "
            f"{evidence_counts.get('support_tickets', 0)} support tickets this period.  "
            f"At-risk churn signals are elevated — proactive outreach is advised."
        )
    else:  # executive
        why = (
            f"The decline is concentrated in {localization['primary_product']} customers in "
            f"{localization['primary_region']} ({localization['primary_segment']} segment).  "
            f"The leading attributed cause is {top_cause['cause']} "
            f"({top_cause['pct']:.0f}% of the drop, ${top_cause['amount']:,.0f}).  "
            f"Customer retention risk is elevated on the affected accounts."
        )

    # -- EVIDENCE NOTE (role-specific) --------------------------------------
    if role == "engineering":
        evidence_note = (
            f"Evidence: {evidence_counts.get('incidents', 0)} engineering incident(s) logged, "
            f"{evidence_counts.get('slack_messages', 0)} escalation Slack thread(s).  "
            f"Gateway telemetry confirms the error-rate spike in gateway_latency_logs "
            f"(Aug 22-26 window)."
        )
    elif role == "customer_support":
        evidence_note = (
            f"{evidence_counts.get('support_tickets', 0)} ticket(s) received from affected "
            f"customers.  Categories: payment_failure and pricing.  "
            f"Sales call notes corroborate customer frustration."
        )
    else:  # executive
        evidence_note = (
            f"This is supported by {evidence_counts.get('support_tickets', 0)} support "
            f"ticket(s), {evidence_counts.get('slack_messages', 0)} engineering Slack "
            f"message(s), and {evidence_counts.get('incidents', 0)} confirmed engineering "
            f"incident(s)."
        )

    result  = {"what": what, "why": why, "evidence_note": evidence_note, "role": role}
    prompt  = str(anomaly) + str(localization) + str(attribution) + str(evidence_counts)
    output  = what + " " + why + " " + evidence_note
    latency = (time.perf_counter() - t0) * 1000
    return _with_telemetry(result, prompt, output, latency)


def generate_recommendation_narrative(
    recommendation,
    role: str | None = None,
) -> dict:
    """Produce a plain-language recommendation summary.

    Role-specific framing
    ---------------------
    executive
        ROI, revenue recovery, payback period, customer retention uplift.
    engineering
        Rollback steps, time-to-impact, technical risk.
    customer_support
        What the fix means for customers; avoid financial specifics.
    """
    role = _normalize_role(role)
    t0   = time.perf_counter()

    best = recommendation["recommended"]

    if role == "engineering":
        narrative = (
            f"Recommended action: {best['label']}.  "
            f"Estimated time to production impact: {best['time_to_impact_days']:.0f} day(s).  "
            f"Risk level: {best['risk']}.  "
            f"Once deployed, the payment-api error rate should return to the ~1.2% baseline "
            f"and p99 latency to <300 ms within one rolling-hour window."
        )
    elif role == "customer_support":
        narrative = (
            f"The engineering team is working on: {best['label']}.  "
            f"Affected customers should see payment processing restored within "
            f"{best['time_to_impact_days']:.0f} day(s).  "
            f"CS agents can proactively inform impacted accounts that the issue is "
            f"being resolved and offer goodwill credits as appropriate."
        )
    else:  # executive
        narrative = (
            f"Recommended action: {best['label']}.  "
            f"Expected recovery: ${best['expected_recovery']:,.0f} against a cost of "
            f"${best['cost']:,.0f}, for a net benefit of ${best['net_benefit']:,.0f} "
            f"({best['roi_pct']:.0f}% ROI) within an estimated "
            f"{best['time_to_impact_days']:.0f} days.  "
            f"Acting now avoids an estimated ${recommendation['all_simulations'][0].get('expected_recovery', 0):,.0f} "
            f"in additional customer churn exposure."
        )

    result  = {"narrative": narrative, "role": role}
    prompt  = str(recommendation)
    latency = (time.perf_counter() - t0) * 1000
    return _with_telemetry(result, prompt, narrative, latency)
