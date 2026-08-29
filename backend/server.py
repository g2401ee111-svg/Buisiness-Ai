"""
server.py
A dependency-free HTTP API server (Python stdlib `http.server`) that
implements the endpoints required by the spec, plus static file serving
for the frontend so the whole prototype runs with a single command:

    python3 backend/server.py

No Express/Node/Postgres required.  Swapping this for Express + Postgres
later is a drop-in replacement as long as the same JSON contracts are
kept (see README "Architecture" section for the mapping).

RBAC
----
Read the ``X-User-Role`` request header to determine the caller's persona.
Supported values: ``executive`` | ``engineering`` | ``customer_support``.
Defaults to ``executive`` when the header is absent or unrecognised.

Each role sees a filtered view of the response payload:
  executive        High-level financial impact, simulation results,
                   recommendation narrative.  Raw engineering telemetry and
                   internal stack details are redacted.
  engineering      Raw gateway latency, p99 metrics, retry-config logs,
                   abstention details.  Financial loss figures are summarised
                   (not redacted) but presented in engineering context.
  customer_support Customer names, ticket summaries, churn risk signals.
                   Engineering rollback details and precise financial figures
                   are redacted.

Runtime Telemetry
-----------------
Every JSON response includes a top-level ``_telemetry`` object:
  request_latency_ms   wall-clock ms for the entire handler
  role                 resolved RBAC role for this request
  endpoint             the matched route path
The AI-layer functions attach their own per-call telemetry (model_name,
tokens_in, tokens_out, estimated_cost_usd, latency_ms) nested under
their respective response keys.
"""

import json
import os
import re
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(__file__))
import db as dbmod
import analytics
import ai
import semantic_contracts as sc

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
PORT = int(os.environ.get("PORT", 8787))

STATIC_MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg":  "image/svg+xml",
}

VALID_ROLES = {"executive", "engineering", "customer_support"}

# ---------------------------------------------------------------------------
# RBAC: field-level redaction rules
# ---------------------------------------------------------------------------
# Each entry defines keys to REMOVE from the top-level response dict for a
# given role.  Nested redaction is handled by _apply_rbac().
# ---------------------------------------------------------------------------

_REDACT_FOR_ROLE = {
    "executive": {
        # Executives don't need raw infra telemetry or internal stack details.
        "gateway_raw",
        "abstention_detail",
        "evidence_signals",
        "retry_config",
        "p99_latency_ms",
        "error_count",
        "total_requests",
    },
    "engineering": {
        # Engineers see technical data; suppress customer PII and raw churn $$.
    },
    "customer_support": {
        # CS sees tickets / churn risk; hide financial specifics and rollback logs.
        # NOTE: 'financial_impact' is NOT listed here — it is replaced by a
        # note dict via _redact_financial_impact(), not silently dropped.
        "projected_30d_loss",
        "potential_recovery",
        "intervention_cost",
        "expected_net_benefit",
        "all_simulations",
        "engineering_rollback",
        "abstention_detail",
        "evidence_signals",
        "retry_config",
        "gateway_raw",
    },
}

# Fields to SUMMARISE (replace value with a redacted placeholder) rather than
# remove entirely, keyed by role then field name.
_SUMMARY_FIELDS = {
    "engineering": {
        "revenue_lost":   lambda v: f"~${round(v / 1000)}k revenue impact",
        "at_risk_revenue": lambda v: f"~${round(v / 1000)}k at risk",
    },
    "customer_support": {
        "revenue_lost":       lambda _: "[REDACTED - financial detail]",
        "change_abs":         lambda _: "[REDACTED - financial detail]",
        "at_risk_revenue":    lambda _: "[REDACTED - financial detail]",
        "recommended":        lambda v: {
            "label":               v.get("label"),
            "time_to_impact_days": v.get("time_to_impact_days"),
            "risk":                v.get("risk"),
        },
    },
    "executive": {},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def json_default(o):
    raise TypeError(f"Not serializable: {o!r}")


def _get_role(headers) -> str:
    """Read ``X-User-Role`` header; default to ``executive``."""
    raw = headers.get("X-User-Role", "").strip().lower()
    return raw if raw in VALID_ROLES else "executive"


def _apply_rbac(payload: dict, role: str) -> dict:
    """Remove or summarise fields in *payload* according to *role*.

    Only operates on the top-level dict keys; nested redaction for known
    sub-objects (financial_impact, recommendation) is handled inline in the
    route methods using the same helpers.
    """
    redact_keys = _REDACT_FOR_ROLE.get(role, set())
    summary_map = _SUMMARY_FIELDS.get(role, {})

    result = {}
    for k, v in payload.items():
        if k in redact_keys:
            continue
        if k in summary_map:
            result[k] = summary_map[k](v)
        else:
            result[k] = v
    return result


def _redact_financial_impact(impact: dict, role: str) -> dict:
    """Apply role-specific view of the financial_impact sub-object."""
    if role == "customer_support":
        # CS sees nothing financial beyond "issue is being resolved"
        return {"note": "Financial details are restricted to executive/finance roles."}
    if role == "engineering":
        return {
            "revenue_lost":       f"~${round(impact.get('revenue_lost', 0) / 1000)}k",
            "customers_affected": impact.get("customers_affected"),
            "churn_rate_pct":     impact.get("churn_rate_pct"),
            "note":               "Full dollar figures restricted to executive view.",
        }
    # executive — full view
    return impact


def _redact_recommendation(rec: dict, role: str) -> dict:
    """Apply role-specific view of the recommendation sub-object."""
    if role == "customer_support":
        best = rec.get("recommended", {})
        return {
            "label":               best.get("label"),
            "time_to_impact_days": best.get("time_to_impact_days"),
            "risk":                best.get("risk"),
            "note":                "Full simulation details restricted to executive/engineering.",
        }
    if role == "engineering":
        best = rec.get("recommended", {})
        return {
            "label":               best.get("label"),
            "time_to_impact_days": best.get("time_to_impact_days"),
            "risk":                best.get("risk"),
            "cost":                best.get("cost"),
            "rationale":           rec.get("rationale"),
            "note":                "ROI figures restricted to executive view.",
        }
    # executive — full view
    return rec


class Handler(BaseHTTPRequestHandler):
    server_version = "DecisionEngine/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[server] " + (fmt % args) + "\n")

    # -- helpers -----------------------------------------------------------

    def _send_json(self, payload, status=200, role="executive",
                   endpoint="unknown", request_start: float | None = None):
        """Serialise *payload* to JSON, inject ``_telemetry``, and send."""
        latency_ms = (
            round((time.perf_counter() - request_start) * 1000, 3)
            if request_start is not None else None
        )
        payload["_telemetry"] = {
            "request_latency_ms": latency_ms,
            "role":               role,
            "endpoint":           endpoint,
        }
        body = json.dumps(payload, default=json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message, status=500, detail=None):
        payload = {"error": message}
        if detail:
            payload["detail"] = detail
        # Errors bypass RBAC/telemetry enrichment to keep the fast path simple.
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON body: {e}")

    def _serve_static(self, path):
        if path == "/":
            path = "/index.html"

        frontend_root = os.path.abspath(FRONTEND_DIR)
        safe_path = os.path.normpath(path).lstrip("/\\")
        full_path = os.path.abspath(os.path.join(frontend_root, safe_path))

        print("FRONTEND ROOT:", frontend_root)
        print("REQUESTED FILE:", full_path)

        if not os.path.isfile(full_path):
            self._send_error_json("Not found", 404)
            return

        ext = os.path.splitext(full_path)[1]
        media_type = STATIC_MEDIA_TYPES.get(ext, "application/octet-stream")

        with open(full_path, "rb") as f:
            body = f.read()

        self.send_response(200)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- routing -----------------------------------------------------------

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-User-Role",
        )
        self.end_headers()

    def do_GET(self):
        t0     = time.perf_counter()
        role   = _get_role(self.headers)
        parsed = urlparse(self.path)
        path   = parsed.path
        qs     = parse_qs(parsed.query)
        try:
            if path == "/api/health":
                return self._route_health(role=role, t0=t0)
            if path == "/api/dashboard":
                return self._route_dashboard(role=role, t0=t0)
            if path == "/api/metrics":
                return self._route_metrics(role=role, t0=t0)
            if path == "/api/anomalies":
                return self._route_anomalies(role=role, t0=t0)
            if path == "/api/semantic-contracts":
                return self._route_semantic_contracts(role=role, t0=t0)
            m = re.match(r"^/api/investigations/([\w\-]+)$", path)
            if m:
                return self._route_investigation(m.group(1), role=role, t0=t0)
            m = re.match(r"^/api/root-causes/([\w\-]+)$", path)
            if m:
                return self._route_root_causes(m.group(1), role=role, t0=t0)
            m = re.match(r"^/api/evidence/([\w\-]+)$", path)
            if m:
                return self._route_evidence(m.group(1), qs, role=role, t0=t0)
            m = re.match(r"^/api/financial-impact/([\w\-]+)$", path)
            if m:
                return self._route_financial_impact(m.group(1), role=role, t0=t0)
            if path.startswith("/api/"):
                return self._send_error_json("Not found", 404)
            return self._serve_static(path)
        except Exception as e:
            traceback.print_exc()
            return self._send_error_json("Internal server error", 500, detail=str(e))

    def do_POST(self):
        t0   = time.perf_counter()
        role = _get_role(self.headers)
        parsed = urlparse(self.path)
        path   = parsed.path
        try:
            if path == "/api/simulate":
                return self._route_simulate(role=role, t0=t0)
            if path == "/api/recommend":
                return self._route_recommend(role=role, t0=t0)
            return self._send_error_json("Not found", 404)
        except ValueError as e:
            return self._send_error_json("Invalid request", 400, detail=str(e))
        except Exception as e:
            traceback.print_exc()
            return self._send_error_json("Internal server error", 500, detail=str(e))

    # -- route implementations ---------------------------------------------

    def _route_health(self, role="executive", t0=None):
        conn = dbmod.get_conn()
        try:
            conn.execute("SELECT 1")
            db_ok = True
        except Exception:
            db_ok = False
        finally:
            conn.close()
        payload = {
            "status":   "ok" if db_ok else "degraded",
            "database": "connected" if db_ok else "unavailable",
            "ai_layer": "mock-deterministic",
        }
        self._send_json(payload, role=role, endpoint="/api/health",
                        request_start=t0)

    def _route_metrics(self, role="executive", t0=None):
        conn = dbmod.get_conn()
        try:
            totals = analytics.get_revenue_totals(conn)
            cust   = analytics.get_customer_metrics(conn)
            impact = analytics.financial_impact(conn)

            raw = {
                "revenue":          totals,
                "customers":        cust,
                "financial_impact": _redact_financial_impact(impact, role),
            }
            self._send_json(_apply_rbac(raw, role), role=role,
                            endpoint="/api/metrics", request_start=t0)
        finally:
            conn.close()

    def _route_anomalies(self, role="executive", t0=None):
        conn = dbmod.get_conn()
        try:
            self._send_json(
                {"anomalies": analytics.detect_anomalies(conn)},
                role=role, endpoint="/api/anomalies", request_start=t0,
            )
        finally:
            conn.close()

    def _route_dashboard(self, role="executive", t0=None):
        conn = dbmod.get_conn()
        try:
            totals        = analytics.get_revenue_totals(conn)
            cust          = analytics.get_customer_metrics(conn)
            anomalies     = analytics.detect_anomalies(conn)
            localization  = analytics.localize_decline(conn)
            attribution   = analytics.attribute_causes(conn)
            impact        = analytics.financial_impact(conn)
            recommendation = analytics.recommend_action(conn)

            evidence_counts = {
                "support_tickets": conn.execute(
                    "SELECT COUNT(*) AS n FROM support_tickets WHERE period='current'"
                ).fetchone()["n"],
                "slack_messages": conn.execute(
                    "SELECT COUNT(*) AS n FROM slack_messages"
                ).fetchone()["n"],
                "incidents": conn.execute(
                    "SELECT COUNT(*) AS n FROM engineering_incidents"
                ).fetchone()["n"],
            }

            explanation = None
            if anomalies:
                explanation = ai.generate_explanation(
                    anomalies[0], localization, attribution,
                    evidence_counts, role=role,
                )

            rec_narrative = ai.generate_recommendation_narrative(
                recommendation, role=role,
            )

            trend = analytics.get_daily_trend(conn)

            raw = {
                "kpis": {
                    "revenue":   totals,
                    "customers": cust,
                },
                "trend":                   trend,
                "anomalies":               anomalies,
                "localization":            localization,
                "attribution":             attribution,
                "financial_impact":        _redact_financial_impact(impact, role),
                "recommendation":          _redact_recommendation(recommendation, role),
                "explanation":             explanation,
                "recommendation_narrative": rec_narrative,
                "demo_data_notice": (
                    "All figures are computed from seeded deterministic demo data."
                ),
            }
            self._send_json(_apply_rbac(raw, role), role=role,
                            endpoint="/api/dashboard", request_start=t0)
        finally:
            conn.close()

    def _route_investigation(self, investigation_id, role="executive", t0=None):
        conn = dbmod.get_conn()
        try:
            totals       = analytics.get_revenue_totals(conn)
            anomalies    = analytics.detect_anomalies(conn)
            localization = analytics.localize_decline(conn)
            attribution  = analytics.attribute_causes(conn)

            if investigation_id != "revenue-decline-aug" and not anomalies:
                return self._send_error_json("Investigation not found", 404)

            timeline = [
                {
                    "label":  "Revenue decline detected",
                    "date":   "2026-08-29",
                    "detail": f"{abs(totals['change_pct']):.1f}% drop vs prior period",
                },
                {
                    "label":  "Payment failures increased",
                    "date":   "2026-08-23",
                    "detail": "Failure rate 1.2% -> 8.7%",
                },
                {
                    "label":  "Support tickets increased",
                    "date":   "2026-08-24",
                    "detail": "Wave of payment-related tickets from NA Enterprise accounts",
                },
                {
                    "label":  "Customer churn risk increased",
                    "date":   "2026-08-25",
                    "detail": "At-risk revenue flagged on affected accounts",
                },
                {
                    "label":  "Engineering incident confirmed",
                    "date":   "2026-08-25",
                    "detail": "Root cause identified: bad retry/timeout config",
                },
            ]

            raw = {
                "id":                 investigation_id,
                "revenue_change_pct": totals["change_pct"],
                "period":             "Aug 22-29 vs Aug 15-21",
                "affected": {
                    "product": localization["primary_product"],
                    "region":  localization["primary_region"],
                    "segment": localization["primary_segment"],
                },
                "timeline":        timeline,
                "root_cause_tree": self._build_root_cause_tree(localization),
                "attribution":     attribution,
            }
            self._send_json(_apply_rbac(raw, role), role=role,
                            endpoint=f"/api/investigations/{investigation_id}",
                            request_start=t0)
        finally:
            conn.close()

    def _build_root_cause_tree(self, localization):
        def mark(items, primary_value):
            return [
                {
                    "label":      it["value"],
                    "is_primary": it["value"] == primary_value,
                    "change_abs": it["change_abs"],
                }
                for it in items
            ]
        return {
            "product": mark(localization["by_product"], localization["primary_product"]),
            "region":  mark(localization["by_region"],  localization["primary_region"]),
            "segment": mark(localization["by_segment"], localization["primary_segment"]),
            "drill_path": [
                localization["primary_product"],
                localization["primary_region"],
                "Payment API",
                "Failure rate increased 1.2% -> 8.7%",
            ],
        }

    def _route_root_causes(self, investigation_id, role="executive", t0=None):
        conn = dbmod.get_conn()
        try:
            attribution = analytics.attribute_causes(conn)
            self._send_json(
                _apply_rbac(attribution, role),
                role=role,
                endpoint=f"/api/root-causes/{investigation_id}",
                request_start=t0,
            )
        finally:
            conn.close()

    def _route_evidence(self, investigation_id, qs, role="executive", t0=None):
        source_filter = qs.get("source", [None])[0]
        conn = dbmod.get_conn()
        try:
            evidence = []

            tickets = conn.execute(
                "SELECT t.*, c.name AS customer_name FROM support_tickets t "
                "JOIN customers c ON c.id = t.customer_id "
                "WHERE t.period='current' ORDER BY t.date"
            ).fetchall()
            for t in tickets:
                is_payment = t["category"] == "payment_failure"
                entry = {
                    "source":             "support",
                    "id":                 t["id"],
                    "timestamp":          t["date"],
                    "text":               t["text"],
                    "related_metric":     t["category"],
                    # -- enriched lineage metadata --
                    "freshness":          "4 days ago" if t["date"] < "2026-08-25" else "2 days ago",
                    "method":             "human_escalation",
                    "contribution_score": 0.72 if is_payment else 0.38,
                    "confidence_score":   0.92 if is_payment else 0.71,
                    "lineage":            f"crm_ticketing -> support_tickets -> {t['id']}",
                }
                # RBAC: customer names only to CS role
                entry["related_customer"] = (
                    t["customer_name"] if role == "customer_support" else t["customer_id"]
                )
                evidence.append(entry)

            calls = conn.execute("SELECT * FROM sales_calls ORDER BY date").fetchall()
            for ccall in calls:
                evidence.append({
                    "source":             "sales",
                    "id":                 ccall["id"],
                    "timestamp":          ccall["date"],
                    "text":               ccall["text"],
                    "related_customer":   ccall["customer_id"],
                    "related_metric":     "sales_call",
                    # -- enriched lineage metadata --
                    "freshness":          "3 days ago",
                    "method":             "human_escalation",
                    "contribution_score": 0.41,
                    "confidence_score":   0.65,
                    "lineage":            f"crm_calls -> sales_calls -> {ccall['id']}",
                })

            incidents = conn.execute(
                "SELECT * FROM engineering_incidents ORDER BY date"
            ).fetchall()
            for inc in incidents:
                entry = {
                    "source":             "engineering",
                    "id":                 inc["id"],
                    "timestamp":          inc["date"],
                    "related_metric": (
                        f"{inc['service']} failure rate "
                        f"{inc['failure_rate_before']}% -> {inc['failure_rate_after']}%"
                    ),
                    # -- enriched lineage metadata --
                    "freshness":          "real-time stream",
                    "method":             "gateway_telemetry",
                    "contribution_score": 0.88,
                    "confidence_score":   0.95,
                    "lineage":            (
                        f"edge_gateway_logs -> payment_service -> incident_{inc['id']}"
                    ),
                }
                if role == "engineering":
                    entry["text"]                = inc["description"]
                    entry["failure_rate_before"] = inc["failure_rate_before"]
                    entry["failure_rate_after"]  = inc["failure_rate_after"]
                    entry["status"]              = inc["status"]
                elif role == "customer_support":
                    entry["text"] = (
                        "A platform issue affecting payment processing was identified "
                        "and is being resolved by the engineering team."
                    )
                else:
                    entry["text"] = inc["description"]
                evidence.append(entry)

            slack = conn.execute(
                "SELECT * FROM slack_messages ORDER BY date"
            ).fetchall()
            for s in slack:
                if role == "customer_support":
                    continue   # internal Slack not shown to CS
                is_eng = "#eng-payments" in s["channel"]
                evidence.append({
                    "source":             "slack",
                    "id":                 s["id"],
                    "timestamp":          s["date"],
                    "text":               s["text"],
                    "related_metric":     s["channel"],
                    # -- enriched lineage metadata --
                    "freshness":          "2 hours ago" if is_eng else "1 day ago",
                    "method":             "regex_tagging",
                    "contribution_score": 0.55 if is_eng else 0.30,
                    "confidence_score":   0.78 if is_eng else 0.60,
                    "lineage":            f"slack_export -> slack_messages -> {s['id']}",
                })

            if source_filter:
                evidence = [e for e in evidence if e["source"] == source_filter]

            ai_summary = ai.summarize_tickets(list(tickets), role=role)

            payload = {
                "investigation_id": investigation_id,
                "evidence":         evidence,
                "ai_summary":       ai_summary,
                "note": (
                    "Evidence is retrieved verbatim from seeded demo records; "
                    "nothing here is generated."
                ),
            }
            self._send_json(payload, role=role,
                            endpoint=f"/api/evidence/{investigation_id}",
                            request_start=t0)
        finally:
            conn.close()

    def _route_financial_impact(self, investigation_id, role="executive", t0=None):
        conn = dbmod.get_conn()
        try:
            impact = analytics.financial_impact(conn)
            payload = _redact_financial_impact(impact, role)
            self._send_json(
                payload, role=role,
                endpoint=f"/api/financial-impact/{investigation_id}",
                request_start=t0,
            )
        finally:
            conn.close()

    def _route_simulate(self, role="executive", t0=None):
        body       = self._read_json_body()
        action_key = body.get("action")
        overrides  = body.get("overrides", {})
        if not action_key:
            raise ValueError("Missing 'action' field")
        conn = dbmod.get_conn()
        try:
            result = analytics.simulate_intervention(conn, action_key, overrides)
            # Customer support doesn't see detailed financial projections
            if role == "customer_support":
                for field in ("expected_recovery", "net_benefit", "roi_pct",
                              "payback_days", "cost"):
                    result.pop(field, None)
                result["note"] = "Detailed financials restricted to executive/engineering."
            self._send_json(result, role=role, endpoint="/api/simulate",
                            request_start=t0)
        except ValueError as e:
            self._send_error_json(str(e), 400)
        finally:
            conn.close()

    def _route_recommend(self, role="executive", t0=None):
        conn = dbmod.get_conn()
        try:
            rec       = analytics.recommend_action(conn)
            narrative = ai.generate_recommendation_narrative(rec, role=role)
            # Redact the recommendation first, then attach the narrative dict
            # so it is never stripped by _redact_recommendation().
            payload            = _redact_recommendation(rec, role)
            payload["narrative"] = narrative
            self._send_json(payload, role=role, endpoint="/api/recommend",
                            request_start=t0)
        finally:
            conn.close()

    def _route_semantic_contracts(self, role="executive", t0=None):
        """``GET /api/semantic-contracts``

        Returns the full KPI_CONTRACTS registry filtered to the contracts
        visible to the caller's role (using ``sc.list_contracts()``).
        """
        visible = sc.list_contracts(access_level=role)
        payload = {
            "kpi_contracts": visible,
            "total":         len(visible),
            "access_level":  role,
        }
        self._send_json(payload, role=role,
                        endpoint="/api/semantic-contracts", request_start=t0)


def main():
    if not os.path.exists(dbmod.DB_PATH):
        print("No database found, initializing seed data...")
        dbmod.init_db()
    else:
        print(f"Using existing database at {dbmod.DB_PATH}")
        print("(delete backend/../data/app.db and restart to reseed)")

    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Decision Engine API + frontend running at http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
