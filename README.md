# Buisiness.ai — AI-Powered Business Decision Engine

A working local prototype that turns "revenue dropped" into
**WHAT → WHY → HOW MUCH → WHAT NEXT → WHAT IF**, backed by a real
(seeded, deterministic) database and real arithmetic — not a mockup.

Demo path: **Revenue ↓ 6% → Root Cause → Evidence → Financial Loss →
Simulation → Recommended Action.**

---

## 1. Quick start

Requirements: **Python 3.9+** only. No Node, no npm install, no
database server to stand up.

```bash
cd backend
python3 server.py
```

Then open **http://localhost:8787** in a browser (one with internet
access, since the page loads React from a CDN — see "A note on the
tech stack" below).

The first run seeds `data/app.db` (SQLite) automatically. To reset the
demo data at any point:

```bash
rm data/app.db
python3 backend/server.py
```

### Run the tests

```bash
python3 -m unittest discover -s backend/tests -v
```

19 tests, all passing: unit tests for every deterministic calculation
(revenue %, root-cause attribution, financial impact, ROI, simulation)
plus one full end-to-end test that walks the entire demo scenario.

---

## 2. A note on the tech stack (read this first)

The original spec asks for **React + TypeScript + Tailwind + Recharts**
on the frontend and **Express + PostgreSQL** on the backend. This
prototype was built and tested inside a sandboxed environment with
**no network access for package installation** (`npm install` /
`pip install` couldn't reach the registries). Rather than hand you
code that was never actually run, I built the equivalent functionality
using only what could be installed *and verified* in that sandbox —
and I ran it: seeded the DB, hit every endpoint, ran the full test
suite, checked the served HTML/JS.

Concretely, this is what changed and why it's a safe substitution:

| Spec asked for | This prototype uses | Why | How to upgrade |
|---|---|---|---|
| Express (Node) API | Python stdlib `http.server` | Zero dependencies to install; identical JSON contracts | Point an Express app at the same routes in `backend/server.py` — the SQL in `analytics.py` ports almost directly |
| PostgreSQL | SQLite (stdlib `sqlite3`) | The spec explicitly allows this as a fallback ("If PostgreSQL is difficult to run locally, provide a SQLite fallback") | Swap `db.py`'s connection layer for `psycopg2`/`asyncpg`; the schema in `SCHEMA` is portable SQL |
| React + TypeScript + build step (Vite/CRA) | React 18 (via CDN `<script>` tags) written with `React.createElement` directly, no JSX/TS/bundler | Runs by opening a URL — no `npm install`, so it could actually be executed and checked in this environment | Copy `frontend/app.js`'s components into `.tsx` files, add prop types, run through Vite |
| Recharts | Hand-rolled SVG line/bar charts | Recharts isn't available without an npm install | Swap `RevenueTrendChart`/`ContributionBars`/`BreakdownBars` for `<LineChart>`/`<BarChart>` from Recharts — the data shapes they consume are already exactly what Recharts expects |
| Tailwind (build-time) | Tailwind via the CDN play script | Same utility classes, no PostCSS build needed | Swap the `<script src="cdn.tailwindcss.com">` for a real Tailwind build; class names don't need to change |

Every calculation, API contract, evidence record, and database
relationship described in the spec is implemented and was tested in
this environment. What differs is *how the code is packaged*, not what
it does. If you have a normal (networked) dev machine, the "How to
upgrade" column is a mechanical migration, not a rewrite — the hard
part (root-cause math, the schema, the evidence linking, the
simulation engine) is already done and tested.

---

## 3. Project structure

```
/backend
  server.py          # HTTP API + serves the frontend statically
  db.py               # SQLite schema + deterministic seed data
  analytics.py         # Deterministic layer: all KPI/root-cause/ROI math
  ai.py                 # Mock AI layer: summarization & narration only
  /tests
    test_analytics.py   # Unit tests + one full end-to-end test
/frontend
  index.html            # Loads React from CDN + app.js, no build step
  app.js                 # Entire UI: dashboard, investigation, simulation
/data
  app.db                 # Created on first run (SQLite)
.env.example
README.md
```

---

## 4. Architecture

### Deterministic layer vs. AI layer (as required by the spec)

- **`analytics.py`** — pure functions and SQL aggregations. Revenue
  totals, percentage change, root-cause dollar attribution, financial
  impact, simulation math, ROI, recommendation ranking. **No LLM call
  anywhere in this file.** Given the same database, every function
  returns byte-identical output every time — this is what the test
  suite checks.
- **`ai.py`** — the "AI layer." Its only job is to *summarize and
  narrate numbers computed elsewhere*: extracting themes from support
  tickets, phrasing the WHAT/WHY sentences on the dashboard, and
  writing the recommendation narrative. It never invents a number —
  every figure it references is passed in from `analytics.py`. This is
  the **deterministic mock AI** the spec calls for when no LLM API key
  is available (rule-based keyword extraction, not random text, so
  outputs are reproducible — see `test_ai_summary_is_deterministic`).
  Swapping in a real LLM later means replacing the bodies of
  `summarize_tickets` / `generate_explanation` with an API call, while
  keeping the same signatures and the same "read-only with respect to
  numbers" contract.

### Data model

SQLite tables, connected by shared IDs exactly as the spec requires
(`customer_id`, `product_id`, `region_id`, `period`/`date`):

- **Structured:** `customers`, `products`, `regions`, `transactions`
- **Unstructured:** `support_tickets`, `sales_calls`,
  `slack_messages`, `engineering_incidents`

Every unstructured record links back to a real `customer_id` /
`product_id` / `region_id`, so the "Evidence" panel can show *why* a
number moved, not just that it moved.

### API endpoints (all implemented)

```
GET  /api/health
GET  /api/dashboard              # everything the dashboard needs, one call
GET  /api/metrics
GET  /api/anomalies
GET  /api/investigations/:id
GET  /api/root-causes/:id
GET  /api/evidence/:id?source=support|sales|engineering|slack
GET  /api/financial-impact/:id
POST /api/simulate    { action, overrides? }
POST /api/recommend
```

All responses are JSON. Errors return `{"error": "...", "detail": "..."}`
with an appropriate HTTP status (400 for bad input, e.g. an unknown
`action` in `/api/simulate`; 404 for missing routes; 500 with a caught
traceback for unexpected server errors — nothing crashes the process).

---

## 5. The demo scenario (deterministic, same every run)

Seed data is hand-authored, not randomly generated, so this is exactly
reproducible:

- Previous period revenue: **$1,000,000** (Aug 15–21)
- Current period revenue: **$939,999.96** (Aug 22–29) → **‑6.0%**
- Most affected: **Product B**, **North America**, **Enterprise**
  segment (12 customers, all flagged with a payment failure)
- Root cause breakdown (computed, not hand-set):
  **Payment API failures (~58%)**, pricing issue, delivery delays, other
- Evidence: 12 support tickets referencing payment failures, 2 sales
  calls, 5 Slack messages from the engineering team diagnosing a bad
  retry/timeout config, 1 confirmed engineering incident showing the
  payment failure rate rising from 1.2% → 8.7%
- Recommended action (computed by risk-adjusted net benefit across all
  three simulated interventions): **Fix the payment API**

Click any anomaly on the dashboard → **Investigation** page → drill
into the root-cause tree → filter evidence by source → then go to
**Simulation** to adjust cost/recovery-rate/time-to-impact and compare
all three interventions side by side.

---

## 6. Error handling

- Loading and empty states on every panel (see `Spinner` / `EmptyState`
  / `ErrorState` in `app.js`)
- API validation errors (`400`) for bad simulation input (e.g.
  unknown intervention key)
- `/api/health` reports database connectivity and which AI layer is
  active, so a broken DB or missing AI backend is visible immediately
  rather than silently producing wrong numbers
- All demo data is explicitly labeled ("DEMO DATA" badge, and a
  `demo_data_notice` field in the API response) — nothing is presented
  as live production data
