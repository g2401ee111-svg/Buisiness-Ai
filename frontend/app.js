// app.js
// Northwind Analytics -- AI-Powered Business Decision Engine (frontend)
//
// Written with React.createElement directly (no JSX, no bundler) so the
// whole app runs by opening index.html served from the Python backend --
// zero npm install, zero build step. See README for how to port this to
// a Vite + JSX + Recharts setup if you want the full stack described in
// the original spec.

const { useState, useEffect, useMemo, useCallback, useRef } = React;
const h = React.createElement;

// ---------------------------------------------------------------------
// Persona / Role state — lives at App level, flows down as prop
// ---------------------------------------------------------------------
const ROLES = [
  { key: "executive",        label: "Executive",            avatar: "EX", color: "bg-sky-600" },
  { key: "engineering",      label: "Engineering Lead",     avatar: "EL", color: "bg-violet-600" },
  { key: "customer_support", label: "Customer Support Lead",avatar: "CS", color: "bg-amber-600" },
];

// ---------------------------------------------------------------------
// API client — always forwards X-User-Role header
// ---------------------------------------------------------------------
const API_BASE = "";

function apiGet(path, role) {
  return fetch(API_BASE + path, {
    headers: { "X-User-Role": role || "executive" },
  }).then((res) => {
    if (!res.ok) return res.json().catch(() => ({})).then((b) => { throw new Error(b.error || `Request failed: ${res.status}`); });
    return res.json();
  });
}

function apiPost(path, payload, role) {
  const body = JSON.stringify(payload || {});
  return fetch(API_BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Role": role || "executive" },
    body,
  }).then((res) => {
    if (!res.ok) return res.json().catch(() => ({})).then((b) => { throw new Error(b.error || `Request failed: ${res.status}`); });
    return res.json();
  });
}

// ---------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------
function fmtCurrency(n, opts) {
  if (n === null || n === undefined || Number.isNaN(n) || typeof n === "string") return typeof n === "string" ? n : "—";
  const compact = opts && opts.compact;
  if (compact) {
    const abs = Math.abs(n);
    if (abs >= 1000) return (n < 0 ? "-$" : "$") + (abs / 1000).toFixed(0) + "K";
  }
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

function fmtPct(n, opts) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const signed = opts && opts.signed;
  const sign = signed && n > 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}%`;
}

function fmtNumber(n) {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("en-US");
}

// ---------------------------------------------------------------------
// Small UI primitives
// ---------------------------------------------------------------------
function Badge({ children, tone = "neutral" }) {
  const tones = {
    neutral: "bg-slate-100 text-slate-600",
    good:    "bg-emerald-50 text-emerald-700",
    bad:     "bg-rose-50 text-rose-700",
    warn:    "bg-amber-50 text-amber-700",
    info:    "bg-sky-50 text-sky-700",
    purple:  "bg-violet-50 text-violet-700",
  };
  return h("span", { className: `inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${tones[tone] || tones.neutral}` }, children);
}

function Card({ children, className = "", padded = true }) {
  return h(
    "div",
    { className: `bg-white border border-slate-200 shadow-sm shadow-slate-200/60 rounded-xl ${padded ? "p-5" : ""} ${className}` },
    children
  );
}

function SectionTitle({ eyebrow, title, right }) {
  return h(
    "div",
    { className: "flex items-start justify-between mb-4" },
    h(
      "div",
      null,
      eyebrow && h("div", { className: "text-xs uppercase tracking-wider text-slate-500 font-semibold mb-1" }, eyebrow),
      h("h2", { className: "text-lg font-semibold text-slate-800" }, title)
    ),
    right || null
  );
}

function Spinner({ label }) {
  return h(
    "div",
    { className: "flex items-center gap-3 text-slate-500 text-sm py-10 justify-center" },
    h("div", { className: "w-4 h-4 border-2 border-slate-200 border-t-sky-500 rounded-full animate-spin" }),
    label || "Loading..."
  );
}

function ErrorState({ message, onRetry }) {
  return h(
    "div",
    { className: "text-center py-10" },
    h("p", { className: "text-rose-600 text-sm mb-3" }, message || "Something went wrong."),
    onRetry && h(
      "button",
      { onClick: onRetry, className: "px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-medium" },
      "Retry"
    )
  );
}

function EmptyState({ message }) {
  return h("div", { className: "text-center py-10 text-slate-500 text-sm" }, message || "No data available.");
}

function SourceBadge({ source }) {
  const labels = {
    support:     ["Support ticket", "info"],
    sales:       ["Sales call",     "warn"],
    engineering: ["Engineering",    "bad"],
    slack:       ["Slack",          "neutral"],
  };
  const [label, tone] = labels[source] || [source, "neutral"];
  return h(Badge, { tone }, label);
}

// ---------------------------------------------------------------------
// Edge-case alert badges (sparse history / abstention)
// ---------------------------------------------------------------------
function EdgeCaseBadge({ type }) {
  if (type === "sparse_history") {
    return h(
      "div",
      { className: "flex items-start gap-3 p-4 rounded-xl border border-amber-300 bg-amber-50" },
      h("div", { className: "text-amber-500 text-lg leading-none mt-0.5" }, "⚠"),
      h(
        "div",
        null,
        h("div", { className: "text-sm font-semibold text-amber-800" }, "Sparse history — Inactive baseline"),
        h("div", { className: "text-xs text-amber-700 mt-0.5" },
          "Product D - AI Copilot has fewer than 7 days of transaction history. " +
          "Anomaly detection and trend analysis are withheld to prevent false positives."
        )
      )
    );
  }
  if (type === "abstention") {
    return h(
      "div",
      { className: "flex items-start gap-3 p-4 rounded-xl border border-violet-300 bg-violet-50" },
      h("div", { className: "text-violet-500 text-lg leading-none mt-0.5" }, "🛑"),
      h(
        "div",
        null,
        h("div", { className: "text-sm font-semibold text-violet-800" }, "Model abstained due to low confidence"),
        h("div", { className: "text-xs text-violet-700 mt-0.5" },
          "Confidence (41%) is below the abstention threshold (60%). " +
          "Root-cause attribution is withheld to prevent false intervention."
        )
      )
    );
  }
  return null;
}

// ---------------------------------------------------------------------
// Telemetry bar — compact footer showing inference cost / latency
// ---------------------------------------------------------------------
function TelemetryBar({ telemetry }) {
  if (!telemetry) return null;
  const { request_latency_ms, role, endpoint } = telemetry;
  // AI telemetry is nested inside explanation/_telemetry — look for it
  // in the global last-response store (passed as prop from pages)
  return h(
    "div",
    {
      className:
        "fixed bottom-0 left-0 right-0 z-20 bg-slate-900/95 backdrop-blur border-t border-slate-700 " +
        "px-6 py-2 flex items-center gap-6 text-xs font-mono text-slate-400 overflow-x-auto",
    },
    h("span", { className: "text-slate-500 shrink-0" }, "⚡ TELEMETRY"),
    h(
      "span",
      { className: "flex items-center gap-1.5" },
      h("span", { className: "text-slate-500" }, "latency"),
      h("span", { className: "text-emerald-400 font-semibold" },
        request_latency_ms != null ? `${request_latency_ms.toFixed(1)} ms` : "—"
      )
    ),
    h(
      "span",
      { className: "flex items-center gap-1.5" },
      h("span", { className: "text-slate-500" }, "role"),
      h("span", { className: "text-sky-400" }, role || "—")
    ),
    h(
      "span",
      { className: "flex items-center gap-1.5" },
      h("span", { className: "text-slate-500" }, "endpoint"),
      h("span", { className: "text-slate-300 truncate max-w-[220px]" }, endpoint || "—")
    )
  );
}

function AiTelemetryBar({ aiTelemetry }) {
  if (!aiTelemetry) return null;
  const { model_name, tokens_in, tokens_out, estimated_cost_usd, latency_ms } = aiTelemetry;
  return h(
    "div",
    {
      className:
        "mt-3 flex flex-wrap items-center gap-4 px-3 py-2 rounded-lg bg-slate-800/90 " +
        "text-xs font-mono text-slate-400 border border-slate-700",
    },
    h("span", { className: "text-slate-500 shrink-0" }, "🤖 AI"),
    h("span", null, h("span", { className: "text-slate-500" }, "model "), h("span", { className: "text-sky-400" }, model_name || "—")),
    h("span", null, h("span", { className: "text-slate-500" }, "tokens "), h("span", { className: "text-slate-300" }, `${tokens_in || 0}in / ${tokens_out || 0}out`)),
    h("span", null, h("span", { className: "text-slate-500" }, "cost "), h("span", { className: "text-emerald-400" }, estimated_cost_usd != null ? `$${estimated_cost_usd.toFixed(6)}` : "—")),
    h("span", null, h("span", { className: "text-slate-500" }, "ai-lat "), h("span", { className: "text-emerald-400" }, latency_ms != null ? `${latency_ms.toFixed(1)} ms` : "—"))
  );
}

// ---------------------------------------------------------------------
// Charts (hand-rolled SVG)
// ---------------------------------------------------------------------
function RevenueTrendChart({ trend, height = 220 }) {
  if (!trend || trend.length === 0) return h(EmptyState, { message: "No trend data." });

  const width = 720;
  const padding = { top: 16, right: 16, bottom: 28, left: 56 };
  const innerW = width - padding.left - padding.right;
  const innerH = height - padding.top - padding.bottom;

  const values = trend.map((d) => d.revenue);
  const min = Math.min(...values) * 0.97;
  const max = Math.max(...values) * 1.03;

  const x = (i) => padding.left + (i / (trend.length - 1)) * innerW;
  const y = (v) => padding.top + innerH - ((v - min) / (max - min)) * innerH;

  const linePath = trend.map((d, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(1)} ${y(d.revenue).toFixed(1)}`).join(" ");
  const areaPath = `${linePath} L ${x(trend.length - 1).toFixed(1)} ${padding.top + innerH} L ${x(0)} ${padding.top + innerH} Z`;

  const splitIndex = trend.findIndex((d) => d.period === "current");
  const gridLines = 4;
  const gridYs = Array.from({ length: gridLines + 1 }, (_, i) => padding.top + (i / gridLines) * innerH);

  return h(
    "svg",
    { viewBox: `0 0 ${width} ${height}`, className: "w-full h-auto", role: "img", "aria-label": "Revenue trend chart" },
    h("defs", null,
      h("linearGradient", { id: "areaFill", x1: "0", y1: "0", x2: "0", y2: "1" },
        h("stop", { offset: "0%", stopColor: "#38bdf8", stopOpacity: 0.25 }),
        h("stop", { offset: "100%", stopColor: "#38bdf8", stopOpacity: 0 })
      )
    ),
    gridYs.map((gy, i) => h("line", { key: `grid-${i}`, x1: padding.left, x2: width - padding.right, y1: gy, y2: gy, stroke: "#e2e8f0", strokeWidth: 1 })),
    gridYs.map((gy, i) => {
      const val = max - (i / gridLines) * (max - min);
      return h("text", { key: `gy-label-${i}`, x: padding.left - 8, y: gy + 3, textAnchor: "end", fontSize: 10, fill: "#64748b" }, fmtCurrency(val, { compact: true }));
    }),
    splitIndex > 0 && h("line", { x1: x(splitIndex), x2: x(splitIndex), y1: padding.top, y2: padding.top + innerH, stroke: "#94a3b8", strokeDasharray: "4 3", strokeWidth: 1 }),
    h("path", { d: areaPath, fill: "url(#areaFill)" }),
    h("path", { d: linePath, fill: "none", stroke: "#0ea5e9", strokeWidth: 2.5 }),
    trend.map((d, i) => {
      const isAnomaly = d.is_anomaly;
      return h("g", { key: `pt-${i}` },
        isAnomaly && h("circle", { cx: x(i), cy: y(d.revenue), r: 9, fill: "#fb7185", opacity: 0.18 }),
        h("circle", { cx: x(i), cy: y(d.revenue), r: isAnomaly ? 4.5 : 3, fill: isAnomaly ? "#fb7185" : "#0ea5e9", stroke: "#ffffff", strokeWidth: 1.5 }),
        isAnomaly && h("text", { x: x(i), y: y(d.revenue) - 14, textAnchor: "middle", fontSize: 9, fontWeight: 700, fill: "#e11d48" }, "ANOMALY")
      );
    }),
    trend.map((d, i) => i % 2 === 0 ? h("text", { key: `x-${i}`, x: x(i), y: height - 8, textAnchor: "middle", fontSize: 10, fill: "#64748b" }, d.date) : null)
  );
}

function ContributionBars({ items, valueKey = "amount", labelKey = "cause", pctKey = "pct" }) {
  if (!items || items.length === 0) return h(EmptyState, {});
  const maxVal = Math.max(...items.map((it) => it[valueKey]));
  return h(
    "div",
    { className: "space-y-3" },
    items.map((it, idx) => {
      const widthPct = maxVal > 0 ? (it[valueKey] / maxVal) * 100 : 0;
      return h(
        "div",
        { key: idx },
        h("div", { className: "flex items-center justify-between text-sm mb-1" },
          h("span", { className: "text-slate-700 font-medium" }, `${idx + 1}. ${it[labelKey]}`),
          h("span", { className: "text-slate-500 font-mono text-xs" }, `${fmtCurrency(it[valueKey], { compact: true })} · ${it[pctKey].toFixed(0)}%`)
        ),
        h("div", { className: "h-2.5 rounded-full bg-slate-100 overflow-hidden" },
          h("div", { className: "h-full rounded-full", style: { width: `${widthPct}%`, background: idx === 0 ? "linear-gradient(90deg,#38bdf8,#0ea5e9)" : "#cbd5e1" } })
        )
      );
    })
  );
}

function BreakdownBars({ items }) {
  if (!items || items.length === 0) return h(EmptyState, {});
  const maxAbs = Math.max(...items.map((it) => Math.abs(it.change_abs)), 1);
  return h(
    "div",
    { className: "space-y-2.5" },
    items.map((it, idx) => {
      const isNegative = it.change_abs < 0;
      const widthPct = (Math.abs(it.change_abs) / maxAbs) * 100;
      return h(
        "div",
        { key: idx, className: "flex items-center gap-3 text-sm" },
        h("span", { className: "w-32 shrink-0 text-slate-600 truncate" }, it.value),
        h("div", { className: "flex-1 h-2 rounded-full bg-slate-100 overflow-hidden" },
          h("div", { className: `h-full rounded-full ${isNegative ? "bg-rose-500/70" : "bg-emerald-500/70"}`, style: { width: `${Math.max(widthPct, 2)}%` } })
        ),
        h("span", { className: `w-24 text-right font-mono text-xs ${isNegative ? "text-rose-600" : "text-emerald-600"}` },
          `${isNegative ? "" : "+"}${fmtCurrency(it.change_abs, { compact: true })}`
        )
      );
    })
  );
}

// ---------------------------------------------------------------------
// Dashboard building blocks
// ---------------------------------------------------------------------
function KPICard({ label, value, delta, deltaTone, sub }) {
  return h(
    Card,
    { className: "flex flex-col gap-2" },
    h("div", { className: "text-xs uppercase tracking-wider text-slate-500 font-semibold" }, label),
    h("div", { className: "text-2xl font-bold text-slate-900 font-mono" }, value),
    (delta || sub) && h(
      "div",
      { className: "flex items-center gap-2 text-xs" },
      delta && h(Badge, { tone: deltaTone || "neutral" }, delta),
      sub && h("span", { className: "text-slate-500" }, sub)
    )
  );
}

function KPIRow({ dashboard, role }) {
  const rev = dashboard.kpis.revenue;
  const cust = dashboard.kpis.customers;
  const impact = dashboard.financial_impact;

  // Engineering: fewer financial KPI cards, add a technical status card
  if (role === "engineering") {
    return h(
      "div",
      { className: "grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-4" },
      h(KPICard, { label: "Revenue", value: fmtCurrency(rev.current, { compact: true }), delta: fmtPct(rev.change_pct, { signed: true }), deltaTone: rev.change_pct < 0 ? "bad" : "good" }),
      h(KPICard, { label: "Customers Affected", value: fmtNumber(cust.customers_affected), sub: `of ${cust.total_customers} total`, deltaTone: "warn" }),
      h(KPICard, { label: "Churn Rate", value: fmtPct(cust.churn_rate_pct), deltaTone: cust.churn_rate_pct > 10 ? "warn" : "good" }),
      h(KPICard, { label: "Incident Status", value: "In Progress", sub: "payment-api rollback", deltaTone: "warn" })
    );
  }

  // Customer Support: customer-centric view
  if (role === "customer_support") {
    return h(
      "div",
      { className: "grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-4" },
      h(KPICard, { label: "Customers", value: fmtNumber(cust.total_customers), sub: `${cust.customers_affected} affected` }),
      h(KPICard, { label: "Churn Risk", value: fmtPct(cust.churn_rate_pct), deltaTone: cust.churn_rate_pct > 10 ? "warn" : "good" }),
      h(KPICard, { label: "At-Risk Revenue", value: typeof impact.revenue_lost === "string" ? impact.revenue_lost : "[see finance]", sub: "contact finance for exact figure" }),
      h(KPICard, { label: "Open Tickets", value: "10", sub: "payment-related, current period", deltaTone: "warn" })
    );
  }

  // Executive: full financial overview
  return h(
    "div",
    { className: "grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4" },
    h(KPICard, { label: "Revenue", value: fmtCurrency(rev.current, { compact: true }), delta: fmtPct(rev.change_pct, { signed: true }), deltaTone: rev.change_pct < 0 ? "bad" : "good" }),
    h(KPICard, { label: "Revenue Change", value: fmtCurrency(rev.change_abs, { compact: true }), sub: "vs prior period" }),
    h(KPICard, { label: "Customers", value: fmtNumber(cust.total_customers), sub: `${cust.customers_affected} affected` }),
    h(KPICard, { label: "Churn Rate", value: fmtPct(cust.churn_rate_pct), deltaTone: cust.churn_rate_pct > 10 ? "warn" : "good" }),
    h(KPICard, { label: "At-Risk Revenue", value: fmtCurrency(cust.at_risk_revenue, { compact: true }), deltaTone: "warn" }),
    h(KPICard, { label: "Net Benefit (Best Action)", value: fmtCurrency(dashboard.recommendation.recommended && dashboard.recommendation.recommended.net_benefit, { compact: true }), deltaTone: "good" })
  );
}

function RootCausePanel({ attribution, onSelectCause, selectedCause }) {
  return h(
    Card,
    null,
    h(SectionTitle, { eyebrow: "Step 3 · Attribute", title: `Root causes of ${fmtCurrency(attribution.total_drop, { compact: true })} revenue decline` }),
    h(ContributionBars, { items: attribution.causes }),
    h(
      "div",
      { className: "mt-4 flex flex-wrap gap-2" },
      attribution.causes.map((c) => h(
        "button",
        {
          key: c.cause,
          onClick: () => onSelectCause(c.cause),
          className: `text-xs px-2.5 py-1 rounded-full border transition-colors ${
            selectedCause === c.cause ? "bg-sky-500/20 border-sky-500 text-sky-800" : "border-slate-300 text-slate-500 hover:border-slate-400"
          }`,
        },
        c.cause
      ))
    )
  );
}

// ---------------------------------------------------------------------
// Evidence card — enriched with lineage metadata badges
// ---------------------------------------------------------------------
function ConfidenceScorePip({ score }) {
  // score is a float 0-1; render as a coloured pill
  const pct = Math.round((score || 0) * 100);
  const tone = pct >= 85 ? "good" : pct >= 65 ? "warn" : "bad";
  return h(Badge, { tone }, `${pct}% conf`);
}

function ContributionPip({ score }) {
  const pct = Math.round((score || 0) * 100);
  return h(Badge, { tone: "info" }, `${pct}% contribution`);
}

function MethodBadge({ method }) {
  const labels = {
    human_escalation: ["Human escalation", "warn"],
    gateway_telemetry: ["Gateway telemetry", "bad"],
    regex_tagging: ["Regex tagging", "neutral"],
  };
  const [label, tone] = labels[method] || [method, "neutral"];
  return h(Badge, { tone }, label);
}

function LineageTag({ lineage }) {
  const [expanded, setExpanded] = useState(false);
  if (!lineage) return null;
  const parts = lineage.split(" -> ");
  return h(
    "button",
    {
      onClick: () => setExpanded((x) => !x),
      className: "text-left text-xs text-sky-600 hover:text-sky-800 font-mono underline decoration-dotted truncate max-w-full",
      title: lineage,
    },
    expanded
      ? h(
          "span",
          { className: "flex flex-wrap items-center gap-1" },
          parts.map((p, i) => h(
            React.Fragment, { key: i },
            h("span", { className: "bg-sky-50 border border-sky-200 rounded px-1.5 py-0.5 text-sky-700" }, p),
            i < parts.length - 1 && h("span", { className: "text-slate-400 text-[10px]" }, "→")
          ))
        )
      : `📎 ${lineage}`
  );
}

function EvidenceCard({ item }) {
  return h(
    Card,
    { className: "space-y-2" },
    // Row 1: source + method
    h(
      "div",
      { className: "flex items-center justify-between flex-wrap gap-1" },
      h("div", { className: "flex items-center gap-1.5 flex-wrap" },
        h(SourceBadge, { source: item.source }),
        item.method && h(MethodBadge, { method: item.method })
      ),
      h("div", { className: "flex items-center gap-1.5 flex-wrap" },
        item.confidence_score != null
          ? h(ConfidenceScorePip, { score: item.confidence_score })
          : null,
        item.contribution_score != null
          ? h(ContributionPip, { score: item.contribution_score })
          : null
      )
    ),
    // Body text
    item.text && h("p", { className: "text-sm text-slate-700 leading-relaxed" }, item.text),
    // Row 2: timestamp + freshness + metric
    h(
      "div",
      { className: "flex items-center justify-between text-xs text-slate-500 font-mono flex-wrap gap-1" },
      h("div", { className: "flex items-center gap-2" },
        h("span", null, item.timestamp),
        item.freshness && h(Badge, { tone: item.freshness === "real-time stream" ? "good" : "neutral" }, item.freshness)
      ),
      item.related_metric && h("span", null, item.related_metric)
    ),
    // Row 3: lineage
    item.lineage && h(LineageTag, { lineage: item.lineage })
  );
}

function EvidencePanel({ evidence, aiSummary, filter, onFilterChange }) {
  const filters = ["all", "support", "sales", "engineering", "slack"];
  const shown = filter === "all" ? evidence : evidence.filter((e) => e.source === filter);
  const aiTel = aiSummary && aiSummary._telemetry;
  return h(
    Card,
    null,
    h(SectionTitle, { eyebrow: "Step 4 · Explain", title: "Evidence" }),
    aiSummary && aiSummary.summary && h(
      "div",
      { className: "mb-2 p-3 rounded-lg bg-sky-500/10 border border-sky-500/20 text-sm text-sky-800" },
      h("span", { className: "font-semibold" }, "AI summary: "), aiSummary.summary
    ),
    aiTel && h(AiTelemetryBar, { aiTelemetry: aiTel }),
    h("div", { className: "mt-4 mb-4 flex gap-2 flex-wrap" },
      filters.map((f) => h(
        "button",
        {
          key: f,
          onClick: () => onFilterChange(f),
          className: `text-xs px-3 py-1.5 rounded-full border capitalize transition-colors ${
            filter === f ? "bg-sky-600 border-sky-600 text-white" : "border-slate-200 text-slate-500 hover:border-slate-300"
          }`,
        },
        f
      ))
    ),
    shown.length === 0
      ? h(EmptyState, { message: "No evidence for this filter." })
      : h("div", { className: "grid md:grid-cols-2 gap-3" }, shown.map((item) => h(EvidenceCard, { key: item.source + item.id, item })))
  );
}

function FinancialImpactPanel({ impact, role }) {
  if (role === "customer_support") {
    // CS sees a note only
    return h(
      Card,
      null,
      h(SectionTitle, { eyebrow: "Step 5 · Quantify", title: "Financial impact" }),
      h("p", { className: "text-sm text-slate-500 italic" }, impact && impact.note ? impact.note : "Financial details are restricted to executive / finance roles.")
    );
  }

  if (role === "engineering") {
    // Engineering sees summarised figures
    const rows = impact ? [
      ["Revenue lost (est.)", impact.revenue_lost],
      ["Customers affected", impact.customers_affected],
      ["Churn rate", impact.churn_rate_pct != null ? `${impact.churn_rate_pct?.toFixed(2)}%` : null],
      ["Note", impact.note],
    ].filter(([, v]) => v != null) : [];
    return h(
      Card,
      null,
      h(SectionTitle, { eyebrow: "Step 5 · Quantify", title: "Financial impact (summarised)" }),
      h(
        "div",
        { className: "divide-y divide-slate-200" },
        rows.map(([label, value], idx) => h(
          "div",
          { key: idx, className: "flex items-center justify-between py-2.5 text-sm" },
          h("span", { className: "text-slate-500" }, label),
          h("span", { className: "font-mono font-semibold text-slate-800" }, String(value))
        ))
      )
    );
  }

  // Executive: full view
  const rows = [
    ["Current loss",        impact.revenue_lost,         "bad"],
    ["Projected 30-day loss", impact.projected_30d_loss, "bad"],
    ["Customers affected",  impact.customers_affected,    null],
    ["Revenue at risk",     impact.at_risk_revenue,       "warn"],
    ["Potential recovery",  impact.potential_recovery,    "good"],
    ["Intervention cost",   impact.intervention_cost,     null],
    ["Expected net benefit",impact.expected_net_benefit,  "good"],
  ];
  return h(
    Card,
    null,
    h(SectionTitle, { eyebrow: "Step 5 · Quantify", title: "Financial impact" }),
    h(
      "div",
      { className: "divide-y divide-slate-200" },
      rows.map(([label, value, tone], idx) => h(
        "div",
        { key: idx, className: "flex items-center justify-between py-2.5 text-sm" },
        h("span", { className: "text-slate-500" }, label),
        h("span", {
          className: `font-mono font-semibold ${tone === "bad" ? "text-rose-600" : tone === "good" ? "text-emerald-600" : tone === "warn" ? "text-amber-600" : "text-slate-800"}`,
        }, typeof value === "number" && label !== "Customers affected" ? fmtCurrency(value) : fmtNumber(value))
      ))
    )
  );
}

function RecommendationCard({ recommendation, narrative, onSimulate, role }) {
  const best = recommendation.recommended || recommendation;
  // Guard: CS and engineering get condensed view
  const narText = narrative && narrative.narrative ? narrative.narrative : (typeof narrative === "string" ? narrative : recommendation.rationale);
  const narTel  = narrative && narrative._telemetry;

  return h(
    Card,
    { className: "border-sky-500/30 bg-gradient-to-br from-sky-500/10 to-transparent" },
    h(SectionTitle, { eyebrow: "Step 6 · Recommended Action", title: best.label || "Recommendation" }),
    h("p", { className: "text-sm text-slate-600 mb-3 leading-relaxed" }, narText),
    narTel && h(AiTelemetryBar, { aiTelemetry: narTel }),
    // Show ROI grid only for executive (engineering/CS get condensed)
    role === "executive" && best.expected_recovery != null && h(
      "div",
      { className: "grid grid-cols-2 md:grid-cols-4 gap-3 mt-4 mb-4" },
      [
        ["Expected recovery", fmtCurrency(best.expected_recovery, { compact: true })],
        ["Cost",              fmtCurrency(best.cost, { compact: true })],
        ["ROI",               best.roi_pct != null ? `${best.roi_pct.toFixed(0)}%` : "—"],
        ["Time to impact",    best.time_to_impact_days != null ? `${best.time_to_impact_days.toFixed(0)}d` : "—"],
      ].map(([label, value], idx) => h(
        "div", { key: idx, className: "bg-sky-50 rounded-lg p-3" },
        h("div", { className: "text-[11px] uppercase tracking-wide text-slate-500 mb-1" }, label),
        h("div", { className: "text-base font-bold font-mono text-slate-900" }, value)
      ))
    ),
    role !== "executive" && h(
      "div",
      { className: "flex flex-wrap gap-3 mt-3 mb-4" },
      best.time_to_impact_days != null && h(Badge, { tone: "info" }, `Time to impact: ${best.time_to_impact_days}d`),
      best.risk && h(Badge, { tone: best.risk === "Low" ? "good" : best.risk === "Medium" ? "warn" : "bad" }, `Risk: ${best.risk}`)
    ),
    role !== "customer_support" && h(
      "button",
      {
        onClick: onSimulate,
        className: "w-full md:w-auto px-4 py-2.5 rounded-lg bg-sky-500 hover:bg-sky-600 text-white font-semibold text-sm transition-colors",
      },
      "Simulate Action →"
    )
  );
}

// Engineering-only telemetry card
function EngineeringTelemetryCard() {
  return h(
    Card,
    { className: "border-violet-200 bg-violet-50/50" },
    h(SectionTitle, { eyebrow: "Engineering · Telemetry", title: "Gateway P99 & Error Rate" }),
    h(
      "div",
      { className: "space-y-3 text-sm" },
      h(
        "div",
        { className: "flex items-start gap-3 p-3 rounded-lg bg-white border border-violet-100" },
        h("div", { className: "text-violet-400 text-base mt-0.5" }, "📡"),
        h(
          "div",
          null,
          h("div", { className: "font-semibold text-slate-800" }, "Incident window: Aug 22 – 26"),
          h("div", { className: "text-slate-500 text-xs mt-0.5" }, "P99 latency: 1,060 – 1,978 ms (baseline ~210 ms)"),
          h("div", { className: "text-slate-500 text-xs" }, "Error rate: 7.2% – 13.6% (baseline ~1.2%)"),
          h("div", { className: "text-slate-500 text-xs" }, "Service: payment-api · Root cause: retry/timeout handler misconfiguration")
        )
      ),
      h(
        "div",
        { className: "flex items-start gap-3 p-3 rounded-lg bg-white border border-emerald-100" },
        h("div", { className: "text-emerald-400 text-base mt-0.5" }, "✓"),
        h(
          "div",
          null,
          h("div", { className: "font-semibold text-slate-800" }, "Post Aug 27 baseline"),
          h("div", { className: "text-slate-500 text-xs mt-0.5" }, "P99 latency: 210 – 234 ms · Error rate: 1.2 – 1.8%"),
          h("div", { className: "text-slate-500 text-xs" }, "Rollback confirmed. Monitoring active.")
        )
      )
    )
  );
}

// ---------------------------------------------------------------------
// Answer strip
// ---------------------------------------------------------------------
function AnswerStrip({ dashboard, onOpenInvestigation, role }) {
  const anomaly = dashboard.anomalies[0];
  if (!anomaly) return null;
  const impact  = dashboard.financial_impact;
  const best    = dashboard.recommendation.recommended || dashboard.recommendation;
  const explanation = dashboard.explanation || {};

  // Safe access: engineering/CS might not have full financial impact
  const lostStr   = typeof impact.revenue_lost === "string"
    ? impact.revenue_lost
    : impact.revenue_lost != null ? fmtCurrency(impact.revenue_lost, { compact: true }) : "[see finance]";
  const projStr   = impact.projected_30d_loss != null
    ? fmtCurrency(impact.projected_30d_loss, { compact: true })
    : "[see finance]";
  const recovStr  = best.expected_recovery != null
    ? fmtCurrency(best.expected_recovery, { compact: true })
    : "—";

  const items = [
    ["WHAT?",      explanation.what  || `Revenue changed ${fmtPct(anomaly.change_pct, { signed: true })}.`],
    ["WHY?",       explanation.why   || ""],
    ["HOW MUCH?",  role === "customer_support"
      ? "Revenue details are restricted. Contact Finance."
      : `${lostStr} now, up to ${projStr} over 30 days if unresolved.`],
    ["EVIDENCE?",  explanation.evidence_note || ""],
    ["WHAT NEXT?", `${best.label || "Pending action."}`],
    ["WHAT IF?",   role === "customer_support"
      ? "Issue is being resolved. Customers will be notified."
      : `Expected recovery: ${recovStr}.`],
  ];

  return h(
    Card,
    { className: "cursor-pointer hover:border-sky-500/40 transition-colors" },
    h(
      "div",
      { onClick: onOpenInvestigation },
      h(
        "div",
        { className: "flex items-center justify-between mb-4" },
        h(
          "div",
          { className: "flex items-center gap-2" },
          h(Badge, { tone: "bad" }, anomaly.severity.toUpperCase()),
          h("span", { className: "text-sm text-slate-600" }, `Revenue ${anomaly.direction} detected — ${anomaly.period_label}`)
        ),
        h("span", { className: "text-xs text-sky-600 font-medium" }, "Open investigation →")
      ),
      h(
        "div",
        { className: "grid md:grid-cols-3 gap-4" },
        items.map(([label, text], idx) => h(
          "div", { key: idx },
          h("div", { className: "text-[11px] uppercase tracking-wider text-sky-600 font-bold mb-1" }, label),
          h("div", { className: "text-sm text-slate-700 leading-snug" }, text)
        ))
      )
    )
  );
}

// ---------------------------------------------------------------------
// Header / Nav — includes persona switcher
// ---------------------------------------------------------------------
function Header({ page, setPage, role, setRole }) {
  const tabs = [
    ["dashboard",    "Dashboard"],
    ["investigation","Investigation"],
    ["simulation",   "Simulation"],
  ];

  const currentRole = ROLES.find((r) => r.key === role) || ROLES[0];

  return h(
    "header",
    { className: "border-b border-slate-200 bg-white/90 backdrop-blur sticky top-0 z-10" },
    h(
      "div",
      { className: "max-w-7xl mx-auto px-6 py-3 flex items-center justify-between flex-wrap gap-3" },
      // Brand
      h(
        "div",
        { className: "flex items-center gap-3" },
        h("div", { className: "w-8 h-8 rounded-lg bg-gradient-to-br from-sky-400 to-sky-600 flex items-center justify-center font-bold text-white text-sm" }, "N"),
        h(
          "div",
          null,
          h("div", { className: "font-bold text-slate-900 leading-none text-sm" }, "Northwind Analytics"),
          h("div", { className: "text-xs text-slate-500" }, "Business Decision Engine")
        )
      ),
      // Navigation tabs
      h(
        "nav",
        { className: "flex gap-1 bg-sky-50 rounded-lg p-1" },
        tabs.map(([key, label]) => h(
          "button",
          {
            key,
            onClick: () => setPage(key),
            className: `px-3.5 py-1.5 rounded-md text-sm font-medium transition-colors ${
              page === key ? "bg-sky-600 text-white" : "text-slate-500 hover:text-slate-700"
            }`,
          },
          label
        ))
      ),
      // Right side: persona switcher + status
      h(
        "div",
        { className: "flex items-center gap-3 flex-wrap" },
        // Persona switcher
        h(
          "div",
          { className: "flex items-center gap-2" },
          h("span", { className: "text-xs text-slate-500 hidden sm:inline" }, "Persona"),
          h(
            "select",
            {
              value: role,
              onChange: (e) => setRole(e.target.value),
              className:
                "text-xs font-medium rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 " +
                "text-slate-700 cursor-pointer focus:outline-none focus:ring-2 focus:ring-sky-400",
            },
            ROLES.map((r) => h("option", { key: r.key, value: r.key }, r.label))
          )
        ),
        // Status
        h(
          "div",
          { className: "flex items-center gap-1.5 text-xs text-slate-500" },
          h("span", { className: "w-2 h-2 rounded-full bg-emerald-400" }),
          "Live demo"
        ),
        // Avatar
        h(
          "div",
          {
            className: `w-8 h-8 rounded-full ${currentRole.color} flex items-center justify-center font-semibold text-white text-xs`,
            title: currentRole.label,
          },
          currentRole.avatar
        )
      )
    )
  );
}

// ---------------------------------------------------------------------
// Pages
// ---------------------------------------------------------------------
function DashboardPage({ setPage, role }) {
  const [dashboard, setDashboard] = useState(null);
  const [evidenceData, setEvidenceData] = useState(null);
  const [error, setError]   = useState(null);
  const [selectedCause, setSelectedCause] = useState(null);
  const [evidenceFilter, setEvidenceFilter] = useState("all");

  const load = useCallback(() => {
    setError(null);
    setDashboard(null);
    apiGet("/api/dashboard", role)
      .then(setDashboard)
      .catch((e) => setError(e.message));
    apiGet("/api/evidence/revenue-decline-aug", role)
      .then(setEvidenceData)
      .catch(() => {});
  }, [role]);

  useEffect(() => { load(); }, [load]);

  if (error) return h(ErrorState, { message: error, onRetry: load });
  if (!dashboard) return h(Spinner, { label: "Loading dashboard..." });

  const reqTelemetry = dashboard._telemetry;

  return h(
    "div",
    { className: "space-y-6 fade-in pb-12" },  // pb-12 = space for fixed telemetry bar
    dashboard.demo_data_notice && h(
      "div",
      { className: "text-xs text-slate-500 flex items-center gap-2" },
      h(Badge, { tone: "info" }, "DEMO DATA"),
      dashboard.demo_data_notice
    ),
    // Edge-case badges
    h(EdgeCaseBadge, { type: "sparse_history" }),
    // KPI row — role-aware
    h(KPIRow, { dashboard, role }),
    h(AnswerStrip, { dashboard, onOpenInvestigation: () => setPage("investigation"), role }),
    h(
      Card,
      null,
      h(SectionTitle, { eyebrow: "Step 1 · Detect", title: "Revenue trend" }),
      h(RevenueTrendChart, { trend: dashboard.trend }),
      h(
        "div",
        { className: "flex gap-4 mt-2 text-xs text-slate-500" },
        h("div", { className: "flex items-center gap-1.5" }, h("span", { className: "w-2 h-2 rounded-full bg-sky-400" }), "Daily revenue"),
        h("div", { className: "flex items-center gap-1.5" }, h("span", { className: "w-2 h-2 rounded-full bg-rose-400" }), "Anomaly")
      )
    ),
    h(
      "div",
      { className: "grid lg:grid-cols-2 gap-6" },
      h(
        Card,
        null,
        h(SectionTitle, { eyebrow: "Step 2 · Localize", title: "Where the decline is concentrated" }),
        h("div", { className: "space-y-5" },
          h("div", null, h("div", { className: "text-xs text-slate-500 mb-2 font-semibold" }, "By region"), h(BreakdownBars, { items: dashboard.localization.by_region })),
          h("div", null, h("div", { className: "text-xs text-slate-500 mb-2 font-semibold" }, "By product"), h(BreakdownBars, { items: dashboard.localization.by_product })),
          h("div", null, h("div", { className: "text-xs text-slate-500 mb-2 font-semibold" }, "By segment"), h(BreakdownBars, { items: dashboard.localization.by_segment }))
        )
      ),
      h(RootCausePanel, { attribution: dashboard.attribution, onSelectCause: setSelectedCause, selectedCause })
    ),
    // Engineering-only telemetry card
    role === "engineering" && h(EngineeringTelemetryCard),
    h(
      "div",
      { className: "grid lg:grid-cols-2 gap-6" },
      h(FinancialImpactPanel, { impact: dashboard.financial_impact, role }),
      h(RecommendationCard, {
        recommendation: dashboard.recommendation,
        narrative: dashboard.recommendation_narrative,
        onSimulate: () => setPage("simulation"),
        role,
      })
    ),
    // Explanation AI telemetry inline
    dashboard.explanation && dashboard.explanation._telemetry && h(
      "div",
      { className: "px-1" },
      h(AiTelemetryBar, { aiTelemetry: dashboard.explanation._telemetry })
    ),
    evidenceData && h(EvidencePanel, {
      evidence: evidenceData.evidence,
      aiSummary: evidenceData.ai_summary,
      filter: evidenceFilter,
      onFilterChange: setEvidenceFilter,
    }),
    // Fixed telemetry bar
    h(TelemetryBar, { telemetry: reqTelemetry })
  );
}

function InvestigationTimeline({ timeline }) {
  return h(
    "div",
    { className: "relative pl-6" },
    h("div", { className: "absolute left-[7px] top-1 bottom-1 w-px bg-sky-600" }),
    timeline.map((step, idx) => h(
      "div",
      { key: idx, className: "relative pb-6 last:pb-0" },
      h("div", { className: "absolute -left-6 top-1 w-3.5 h-3.5 rounded-full bg-sky-400 ring-4 ring-white" }),
      h("div", { className: "text-sm font-semibold text-slate-800" }, step.label),
      h("div", { className: "text-xs text-slate-500 mt-0.5" }, `${step.date} · ${step.detail}`)
    ))
  );
}

function RootCauseTree({ tree }) {
  const branch = (title, items) => h(
    "div",
    { className: "flex-1 min-w-[180px]" },
    h("div", { className: "text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2" }, title),
    h("div", { className: "space-y-1.5" },
      items.map((it, idx) => h(
        "div",
        {
          key: idx,
          className: `text-sm px-2.5 py-1.5 rounded-lg border ${
            it.is_primary ? "border-sky-500 bg-sky-500/10 text-sky-800 font-semibold" : "border-slate-200 text-slate-500"
          }`,
        },
        it.label,
        it.is_primary && h("span", { className: "ml-2 text-[10px] text-sky-600" }, "PRIMARY")
      ))
    )
  );

  return h(
    "div",
    null,
    h("div", { className: "flex flex-wrap gap-4 mb-6" },
      branch("Product", tree.product),
      branch("Region",  tree.region),
      branch("Customer segment", tree.segment)
    ),
    h("div", { className: "flex flex-wrap items-center gap-2 text-sm" },
      tree.drill_path.map((step, idx) => h(
        React.Fragment,
        { key: idx },
        h("span", { className: "px-3 py-1.5 rounded-lg bg-slate-100 text-slate-700" }, step),
        idx < tree.drill_path.length - 1 && h("span", { className: "text-slate-400" }, "↓")
      ))
    )
  );
}

function InvestigationPage({ role }) {
  const [data, setData]             = useState(null);
  const [evidenceData, setEvidenceData] = useState(null);
  const [error, setError]           = useState(null);
  const [evidenceFilter, setEvidenceFilter] = useState("all");

  const load = useCallback(() => {
    setError(null);
    apiGet("/api/investigations/revenue-decline-aug", role).then(setData).catch((e) => setError(e.message));
    apiGet("/api/evidence/revenue-decline-aug", role).then(setEvidenceData).catch(() => {});
  }, [role]);

  useEffect(() => { load(); }, [load]);

  if (error) return h(ErrorState, { message: error, onRetry: load });
  if (!data)  return h(Spinner, { label: "Loading investigation..." });

  const reqTelemetry = data._telemetry;

  return h(
    "div",
    { className: "space-y-6 fade-in pb-12" },
    h(
      Card,
      null,
      h(SectionTitle, { eyebrow: "Investigation overview", title: `Revenue ${fmtPct(data.revenue_change_pct, { signed: true })}` }),
      h(
        "div",
        { className: "grid sm:grid-cols-3 gap-4 text-sm" },
        h("div", null, h("div", { className: "text-slate-500 text-xs mb-1" }, "Period"),  h("div", { className: "text-slate-800 font-medium" }, data.period)),
        h("div", null, h("div", { className: "text-slate-500 text-xs mb-1" }, "Product"), h("div", { className: "text-slate-800 font-medium" }, data.affected.product)),
        h("div", null, h("div", { className: "text-slate-500 text-xs mb-1" }, "Region / Segment"), h("div", { className: "text-slate-800 font-medium" }, `${data.affected.region} / ${data.affected.segment}`))
      )
    ),
    // Engineering-only sparse-history badge
    h(EdgeCaseBadge, { type: "sparse_history" }),
    h(
      "div",
      { className: "grid lg:grid-cols-2 gap-6" },
      h(Card, null, h(SectionTitle, { title: "Investigation timeline" }), h(InvestigationTimeline, { timeline: data.timeline })),
      h(Card, null, h(SectionTitle, { title: "Root cause tree" }),        h(RootCauseTree,         { tree: data.root_cause_tree }))
    ),
    h(
      Card,
      null,
      h(SectionTitle, { title: "Root cause contribution" }),
      h(ContributionBars, { items: data.attribution.causes })
    ),
    evidenceData && h(
      Card,
      null,
      h(SectionTitle, { title: "Evidence explorer" }),
      h(
        "div",
        { className: "flex gap-2 mb-4 flex-wrap" },
        ["all", "support", "sales", "engineering", "slack"].map((f) => h(
          "button",
          {
            key: f,
            onClick: () => setEvidenceFilter(f),
            className: `text-xs px-3 py-1.5 rounded-full border capitalize transition-colors ${
              evidenceFilter === f ? "bg-sky-600 border-sky-600 text-white" : "border-slate-200 text-slate-500 hover:border-slate-300"
            }`,
          },
          f
        ))
      ),
      h(
        "div",
        { className: "grid md:grid-cols-2 gap-3" },
        (evidenceFilter === "all" ? evidenceData.evidence : evidenceData.evidence.filter((e) => e.source === evidenceFilter))
          .map((item) => h(EvidenceCard, { key: item.source + item.id, item }))
      )
    ),
    h(TelemetryBar, { telemetry: reqTelemetry })
  );
}

const INTERVENTION_OPTIONS = [
  { key: "fix_payment_api",   label: "Fix payment API" },
  { key: "customer_discount", label: "Offer affected customers a discount" },
  { key: "increase_support",  label: "Increase support staffing" },
];

function SliderInput({ label, value, min, max, step, onChange, format }) {
  return h(
    "div",
    { className: "mb-4" },
    h(
      "div",
      { className: "flex items-center justify-between mb-1.5" },
      h("label", { className: "text-xs text-slate-500 font-medium" }, label),
      h("span", { className: "text-sm font-mono text-slate-800" }, format ? format(value) : value)
    ),
    h("input", {
      type: "range", min, max, step, value,
      onChange: (e) => onChange(parseFloat(e.target.value)),
      className: "w-full accent-sky-400",
    })
  );
}

function SimulationPage({ role }) {
  const [action, setAction]           = useState("fix_payment_api");
  const [cost, setCost]               = useState(12000);
  const [recoveryRate, setRecoveryRate] = useState(0.9);
  const [churnReduction, setChurnReduction] = useState(65);
  const [timeToImpact, setTimeToImpact] = useState(3);
  const [result, setResult]           = useState(null);
  const [allResults, setAllResults]   = useState({});
  const [error, setError]             = useState(null);
  const [loading, setLoading]         = useState(false);

  const defaultsByAction = {
    fix_payment_api:   { cost: 12000, recoveryRate: 0.9,  churnReduction: 65, timeToImpact: 3 },
    customer_discount: { cost: 35000, recoveryRate: 0.55, churnReduction: 40, timeToImpact: 7 },
    increase_support:  { cost: 18000, recoveryRate: 0.31, churnReduction: 25, timeToImpact: 5 },
  };

  function selectAction(key) {
    setAction(key);
    const d = defaultsByAction[key];
    setCost(d.cost); setRecoveryRate(d.recoveryRate);
    setChurnReduction(d.churnReduction); setTimeToImpact(d.timeToImpact);
  }

  const runSimulation = useCallback(() => {
    setLoading(true); setError(null);
    apiPost("/api/simulate", {
      action,
      overrides: { cost, recovery_rate: recoveryRate, churn_reduction_pct: churnReduction, time_to_impact_days: timeToImpact },
    }, role)
      .then((r) => { setResult(r); setAllResults((prev) => ({ ...prev, [action]: r })); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [action, cost, recoveryRate, churnReduction, timeToImpact, role]);

  useEffect(() => { runSimulation(); }, [action]);          // eslint-disable-line
  useEffect(() => {
    const t = setTimeout(runSimulation, 250);
    return () => clearTimeout(t);
  }, [cost, recoveryRate, churnReduction, timeToImpact]);  // eslint-disable-line
  useEffect(() => {
    Promise.all(INTERVENTION_OPTIONS.map((opt) =>
      apiPost("/api/simulate", { action: opt.key }, role).then((r) => [opt.key, r])
    )).then((entries) => setAllResults(Object.fromEntries(entries)));
  }, [role]);

  const reqTelemetry = result && result._telemetry;

  return h(
    "div",
    { className: "space-y-6 fade-in pb-12" },
    // CS abstention badge
    role === "customer_support" && h(EdgeCaseBadge, { type: "abstention" }),
    h(
      "div",
      { className: "grid lg:grid-cols-[340px,1fr] gap-6" },
      h(
        Card,
        null,
        h(SectionTitle, { title: "What-if simulation" }),
        h(
          "div",
          { className: "flex flex-col gap-2 mb-5" },
          INTERVENTION_OPTIONS.map((opt) => h(
            "button",
            {
              key: opt.key,
              onClick: () => selectAction(opt.key),
              className: `text-left text-sm px-3 py-2.5 rounded-lg border transition-colors ${
                action === opt.key ? "border-sky-500 bg-sky-500/10 text-sky-800" : "border-slate-200 text-slate-600 hover:border-slate-300"
              }`,
            },
            opt.label
          ))
        ),
        h(SliderInput, { label: "Implementation cost",          value: cost,          min: 1000,  max: 60000, step: 500,  onChange: setCost,          format: (v) => fmtCurrency(v, { compact: true }) }),
        h(SliderInput, { label: "Recovery rate",                value: recoveryRate,  min: 0.05,  max: 1,     step: 0.01, onChange: setRecoveryRate,  format: (v) => `${(v * 100).toFixed(0)}%` }),
        h(SliderInput, { label: "Expected churn reduction",     value: churnReduction,min: 0,     max: 100,   step: 1,    onChange: setChurnReduction, format: (v) => `${v.toFixed(0)}%` }),
        h(SliderInput, { label: "Time to implementation (days)",value: timeToImpact,  min: 1,     max: 30,    step: 1,    onChange: setTimeToImpact,  format: (v) => `${v}d` })
      ),
      h(
        "div",
        { className: "space-y-6" },
        error && h(ErrorState, { message: error, onRetry: runSimulation }),
        result && h(
          Card,
          { className: "border-sky-500/30" },
          h(SectionTitle, { title: result.label }),
          loading && h("div", { className: "text-xs text-slate-500 mb-3" }, "Recalculating..."),
          result.note && h("p", { className: "text-sm text-amber-700 bg-amber-50 rounded-lg p-3 mb-3" }, result.note),
          !result.note && h(
            "div",
            { className: "grid grid-cols-2 md:grid-cols-4 gap-3" },
            [
              ["Expected recovery", fmtCurrency(result.expected_recovery, { compact: true }), "good"],
              ["Net benefit",       fmtCurrency(result.net_benefit, { compact: true }),        result.net_benefit >= 0 ? "good" : "bad"],
              ["ROI",               `${result.roi_pct?.toFixed(0)}%`,                          null],
              ["Payback",           result.payback_days ? `${result.payback_days.toFixed(1)}d` : "—", null],
            ].map(([label, value, tone], idx) => h(
              "div", { key: idx, className: "bg-sky-50 rounded-lg p-3" },
              h("div", { className: "text-[11px] uppercase tracking-wide text-slate-500 mb-1" }, label),
              h("div", { className: `text-base font-bold font-mono ${tone === "bad" ? "text-rose-600" : tone === "good" ? "text-emerald-600" : "text-slate-900"}` }, value)
            ))
          ),
          !result.note && h(BeforeAfterBar, { before: 0, after: result.expected_recovery, cost: result.cost })
        ),
        h(Card, null,
          h(SectionTitle, { title: "Compare interventions" }),
          h(ComparisonTable, { results: allResults, role })
        )
      )
    ),
    reqTelemetry && h(TelemetryBar, { telemetry: reqTelemetry })
  );
}

function BeforeAfterBar({ after, cost }) {
  const max = Math.max(after, cost) * 1.15 || 1;
  return h(
    "div",
    { className: "mt-5" },
    h("div", { className: "text-xs text-slate-500 font-semibold mb-2" }, "Cost vs. expected recovery"),
    h(
      "div",
      { className: "space-y-2" },
      h("div", null,
        h("div", { className: "flex justify-between text-xs mb-1" }, h("span", { className: "text-slate-500" }, "Cost"), h("span", { className: "font-mono text-slate-700" }, fmtCurrency(cost, { compact: true }))),
        h("div", { className: "h-3 rounded bg-slate-100" }, h("div", { className: "h-full rounded bg-sky-400", style: { width: `${(cost / max) * 100}%` } }))
      ),
      h("div", null,
        h("div", { className: "flex justify-between text-xs mb-1" }, h("span", { className: "text-slate-500" }, "Expected recovery"), h("span", { className: "font-mono text-emerald-600" }, fmtCurrency(after, { compact: true }))),
        h("div", { className: "h-3 rounded bg-slate-100" }, h("div", { className: "h-full rounded bg-emerald-500", style: { width: `${(after / max) * 100}%` } }))
      )
    )
  );
}

function ComparisonTable({ results, role }) {
  const rows = INTERVENTION_OPTIONS.map((opt) => results[opt.key]).filter(Boolean);
  if (rows.length === 0) return h(Spinner, { label: "Comparing..." });

  // CS role: hide financial columns
  const cols = role === "customer_support"
    ? [["label", "Action"], ["risk", "Risk"], ["time_to_impact_days", "Time to impact"]]
    : [
        ["label",            "Action"],
        ["cost",             "Cost"],
        ["expected_recovery","Recovery"],
        ["roi_pct",          "ROI"],
        ["risk",             "Risk"],
        ["net_benefit",      "Net benefit"],
      ];

  return h(
    "div",
    { className: "overflow-x-auto" },
    h(
      "table",
      { className: "w-full text-sm" },
      h("thead", null, h("tr", { className: "text-left text-slate-500 text-xs uppercase border-b border-slate-200" },
        cols.map(([key, label]) => h("th", { key, className: "py-2 pr-4 font-semibold" }, label))
      )),
      h("tbody", null, rows.map((r, idx) => h(
        "tr", { key: idx, className: "border-b border-slate-200 last:border-0" },
        cols.map(([key]) => {
          if (key === "label")             return h("td", { key, className: "py-2.5 pr-4 text-slate-800 font-medium" }, r.label);
          if (key === "cost")              return h("td", { key, className: "py-2.5 pr-4 font-mono text-slate-600" }, fmtCurrency(r.cost, { compact: true }));
          if (key === "expected_recovery") return h("td", { key, className: "py-2.5 pr-4 font-mono text-emerald-600" }, fmtCurrency(r.expected_recovery, { compact: true }));
          if (key === "roi_pct")           return h("td", { key, className: "py-2.5 pr-4 font-mono text-slate-800" }, `${r.roi_pct?.toFixed(0)}%`);
          if (key === "risk")              return h("td", { key, className: "py-2.5 pr-4" }, h(Badge, { tone: r.risk === "Low" ? "good" : r.risk === "Medium" ? "warn" : "bad" }, r.risk));
          if (key === "net_benefit")       return h("td", { key, className: "py-2.5 pr-4 font-mono font-semibold text-slate-900" }, fmtCurrency(r.net_benefit, { compact: true }));
          if (key === "time_to_impact_days") return h("td", { key, className: "py-2.5 pr-4 font-mono text-slate-700" }, r.time_to_impact_days != null ? `${r.time_to_impact_days}d` : "—");
          return h("td", { key, className: "py-2.5 pr-4" }, String(r[key] ?? "—"));
        })
      )))
    )
  );
}

// ---------------------------------------------------------------------
// App shell
// ---------------------------------------------------------------------
function App() {
  const [page, setPage] = useState("dashboard");
  const [role, setRole] = useState("executive");

  return h(
    "div",
    { className: "min-h-screen" },
    h(Header, { page, setPage, role, setRole }),
    h(
      "main",
      { className: "max-w-7xl mx-auto px-6 py-6" },
      page === "dashboard"    && h(DashboardPage,    { setPage, role }),
      page === "investigation" && h(InvestigationPage, { role }),
      page === "simulation"   && h(SimulationPage,   { role })
    )
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(h(App));
