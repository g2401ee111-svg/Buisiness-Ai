"""
db.py
Database layer for the AI-Powered Business Decision Engine.

Uses plain SQLite (Python stdlib `sqlite3`) so the whole prototype runs
with zero external dependencies. This is the "SQLite fallback" described
in the spec; swapping to PostgreSQL later just means changing the
connection layer, since all queries here use portable SQL.

Everything is DETERMINISTIC: the seed data below is hand-authored (not
randomly generated), so the demo scenario is identical every time the
database is rebuilt. Re-running `init_db()` drops and recreates
everything from scratch.
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "app.db")
DB_PATH = os.path.abspath(DB_PATH)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
DROP TABLE IF EXISTS daily_active_users;
DROP TABLE IF EXISTS gateway_latency_logs;
DROP TABLE IF EXISTS transactions;
DROP TABLE IF EXISTS support_tickets;
DROP TABLE IF EXISTS sales_calls;
DROP TABLE IF EXISTS slack_messages;
DROP TABLE IF EXISTS engineering_incidents;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS regions;

CREATE TABLE regions (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE products (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE customers (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    segment    TEXT NOT NULL,      -- 'SMB' | 'Enterprise'
    region_id  TEXT NOT NULL REFERENCES regions(id),
    product_id TEXT NOT NULL REFERENCES products(id)
);

-- One row per (customer, period) revenue observation.
-- period is 'previous' (Aug 15-21) or 'current' (Aug 22-29).
CREATE TABLE transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    product_id  TEXT NOT NULL REFERENCES products(id),
    region_id   TEXT NOT NULL REFERENCES regions(id),
    period      TEXT NOT NULL,
    date        TEXT NOT NULL,
    amount      REAL NOT NULL,
    failed      INTEGER NOT NULL DEFAULT 0  -- 1 if a payment failure occurred
);

CREATE TABLE support_tickets (
    id          TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    product_id  TEXT NOT NULL REFERENCES products(id),
    region_id   TEXT NOT NULL REFERENCES regions(id),
    date        TEXT NOT NULL,
    category    TEXT NOT NULL,
    text        TEXT NOT NULL,
    period      TEXT NOT NULL
);

CREATE TABLE sales_calls (
    id          TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    date        TEXT NOT NULL,
    text        TEXT NOT NULL
);

CREATE TABLE slack_messages (
    id      TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    author  TEXT NOT NULL,
    date    TEXT NOT NULL,
    text    TEXT NOT NULL
);

CREATE TABLE engineering_incidents (
    id                  TEXT PRIMARY KEY,
    service             TEXT NOT NULL,
    date                TEXT NOT NULL,
    description         TEXT NOT NULL,
    failure_rate_before REAL NOT NULL,
    failure_rate_after  REAL NOT NULL,
    status              TEXT NOT NULL
);

-- KPI: DAU/MAU Engagement (Operational source, daily grain).
-- One row per (customer, date) capturing active usage minutes and session count.
CREATE TABLE daily_active_users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id    TEXT    NOT NULL,
    date           TEXT    NOT NULL,
    active_minutes REAL    NOT NULL,
    session_count  INTEGER NOT NULL
);

-- KPI: Gateway P99 Latency & Payment Error Rate (Telemetry/Infrastructure, hourly grain).
-- One row per (service, hour_timestamp) capturing p99 latency and error counts.
CREATE TABLE gateway_latency_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    service         TEXT    NOT NULL,
    hour_timestamp  TEXT    NOT NULL,
    p99_latency_ms  REAL    NOT NULL,
    error_count     INTEGER NOT NULL,
    total_requests  INTEGER NOT NULL
);
"""


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_conn()
    conn.executescript(SCHEMA)
    seed(conn)
    conn.commit()
    conn.close()


def seed(conn):
    c = conn.cursor()

    # ---- Reference data -------------------------------------------------
    c.executemany("INSERT INTO regions VALUES (?,?)", [
        ("na", "North America"),
        ("eu", "Europe"),
        ("apac", "APAC"),
    ])
    c.executemany("INSERT INTO products VALUES (?,?)", [
        ("prod_a", "Product A"),
        ("prod_b", "Product B"),
        ("prod_c", "Product C"),
        ("prod_d", "Product D - AI Copilot"),  # newly launched; sparse history
    ])

    # ---- Customers --------------------------------------------------
    # 40 customers total. Weighted so Product B / North America / Enterprise
    # is the affected cluster (per the demo scenario), while still
    # having coverage across every other dimension for contrast.
    customers = []

    def add_customers(prefix, n, segment, region, product):
        for i in range(1, n + 1):
            cid = f"{prefix}{i:03d}"
            customers.append((cid, f"{prefix.upper()} Customer {i}", segment, region, product))

    add_customers("entb_na_", 12, "Enterprise", "na", "prod_b")     # affected cluster
    add_customers("smb_na_", 6, "SMB", "na", "prod_b")
    add_customers("entb_eu_", 5, "Enterprise", "eu", "prod_b")
    add_customers("ent_na_a", 4, "Enterprise", "na", "prod_a")
    add_customers("smb_eu_", 5, "SMB", "eu", "prod_a")
    add_customers("ent_apac_", 4, "Enterprise", "apac", "prod_c")
    add_customers("smb_apac_", 4, "SMB", "apac", "prod_c")

    c.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", customers)

    customer_lookup = {row[0]: row for row in customers}

    # ---- Transactions (revenue) -----------------------------------
    # Previous period total = $1,000,000 exactly.
    # Current period total  = $940,000 exactly (-6%, -$60,000).
    # The -$60,000 is distributed so that:
    #   Region:    North America -$35,000
    #   Product:   Product B     -$18,000 (of the NA drop's product mix)
    #   Segment:   Enterprise    -$12,000 (of the drop concentrated further)
    # These are realized via per-customer amount deltas below.

    txns = []
    txn_id = 0

    def add_txn(customer_id, region, product, period, date, amount, failed=0):
        nonlocal txn_id
        txn_id += 1
        txns.append((txn_id, customer_id, product, region, period, date, amount, failed))

    # Baseline previous-period revenue: $1,000,000 across 40 customers.
    # Enterprise NA/prod_b customers: 12 customers x $30,000 = $360,000
    # SMB NA/prod_b customers:         6 customers x $12,000 = $72,000
    # Enterprise EU/prod_b customers:  5 customers x $18,000 = $90,000
    # Enterprise NA/prod_a customers:  4 customers x $25,000 = $100,000
    # SMB EU/prod_a customers:         5 customers x $15,000 = $75,000
    # Enterprise APAC/prod_c:          4 customers x $30,750 = $123,000
    # SMB APAC/prod_c:                 4 customers x $45,000 = $180,000
    # Total = 360000+72000+90000+100000+75000+123000+180000 = 1,000,000

    baseline = {
        "entb_na_": 30000,
        "smb_na_": 12000,
        "entb_eu_": 18000,
        "ent_na_a": 25000,
        "smb_eu_": 15000,
        "ent_apac_": 30750,
        "smb_apac_": 45000,
    }

    for cid, name, segment, region, product in customers:
        prefix = next(p for p in baseline if cid.startswith(p))
        amt = baseline[prefix]
        add_txn(cid, region, product, "previous", "2026-08-18", amt, failed=0)

    prev_total = sum(t[6] for t in txns)
    assert prev_total == 1_000_000, f"previous total = {prev_total}"

    # Current period: apply the decline.
    # entb_na_ (12 Enterprise / NA / Product B customers): the epicenter.
    #   Each loses $2,916.67 on average -> total -$35,000 for this exact
    #   cluster, driven by payment failures on ~70% of them.
    txn_id_before_current = txn_id
    for i, (cid, name, segment, region, product) in enumerate(customers):
        prefix = next(p for p in baseline if cid.startswith(p))
        base = baseline[prefix]
        if prefix == "entb_na_":
            # 12 customers, distribute -$35,000 unevenly; 8 of them fail
            # a payment and lose more, 4 are lightly impacted.
            drop = 3500 if i % 3 != 0 else 1750  # weighted average -> $35,000 total (see below)
            failed = 1 if i % 3 != 0 else 0
        elif prefix == "smb_na_":
            drop = 0
            failed = 0
        elif prefix == "entb_eu_":
            drop = 0
            failed = 0
        else:
            drop = 0
            failed = 0
        add_txn(cid, region, product, "current", "2026-08-26", base - drop, failed=failed)

    c.executemany(
        "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?)", txns
    )
    conn.commit()

    # The manual weighting above is approximate; normalize precisely so the
    # totals match the spec exactly ($940,000 total, -$35,000 NA region,
    # concentrated on Product B / Enterprise).
    conn.commit()

    # Recompute and force-correct the "entb_na_" current-period rows so the
    # NA / Product B / Enterprise cluster drops by EXACTLY $35,000 total,
    # while every other customer stays flat (0 change), and all 12
    # customers are marked as having experienced a payment failure
    # (the incident affected the whole cluster).
    cur = conn.cursor()
    entb_na_ids = [cid for cid, *_ in customers if cid.startswith("entb_na_")]
    per_customer_drop = 35000 / len(entb_na_ids)  # $2,916.67 each
    for idx, cid in enumerate(entb_na_ids):
        # The payment API incident affected the whole cluster; all 12
        # customers are marked as having experienced a payment failure.
        failed = 1
        new_amount = baseline["entb_na_"] - per_customer_drop
        cur.execute(
            "UPDATE transactions SET amount = ?, failed = ? "
            "WHERE customer_id = ? AND period = 'current'",
            (round(new_amount, 2), failed, cid),
        )
    conn.commit()

    cur.execute("SELECT SUM(amount) FROM transactions WHERE period='current'")
    current_total = cur.fetchone()[0]
    # current_total should now be 1,000,000 - 35,000 = 965,000 -- but the
    # spec requires -$60,000 total (-6%), with the remaining -$25,000
    # coming from Product B customers in Europe (pricing issue, secondary
    # cause) and Enterprise SMB drift elsewhere (minor churn, tertiary).
    # Apply those two secondary declines now.

    # Product B / Europe -- pricing issue: -$15,000 across 5 customers.
    entb_eu_ids = [cid for cid, *_ in customers if cid.startswith("entb_eu_")]
    per_eu_drop = 15000 / len(entb_eu_ids)
    for cid in entb_eu_ids:
        cur.execute(
            "UPDATE transactions SET amount = amount - ? WHERE customer_id = ? AND period = 'current'",
            (per_eu_drop, cid),
        )

    # SMB / NA / Product B -- minor delivery-delay related churn: -$10,000 across 6 customers.
    smb_na_ids = [cid for cid, *_ in customers if cid.startswith("smb_na_")]
    per_smb_drop = 10000 / len(smb_na_ids)
    for cid in smb_na_ids:
        cur.execute(
            "UPDATE transactions SET amount = amount - ? WHERE customer_id = ? AND period = 'current'",
            (per_smb_drop, cid),
        )

    conn.commit()

    cur.execute("SELECT SUM(amount) FROM transactions WHERE period='current'")
    current_total = cur.fetchone()[0]
    assert abs(current_total - 940_000) < 1.0, f"current total = {current_total}"

    # ---- Support tickets --------------------------------------------
    tickets = [
        ("t001", "entb_na_001", "prod_b", "na", "2026-08-23", "payment_failure",
         "Our payment failed twice this week when processing the monthly invoice. Getting a generic 'transaction declined' error.", "current"),
        ("t002", "entb_na_002", "prod_b", "na", "2026-08-23", "payment_failure",
         "Payment API keeps timing out on checkout. This is blocking our billing run for the quarter.", "current"),
        ("t003", "entb_na_003", "prod_b", "na", "2026-08-24", "payment_failure",
         "Third failed payment attempt today. Our finance team is asking why invoices aren't clearing.", "current"),
        ("t004", "entb_na_004", "prod_b", "na", "2026-08-24", "payment_failure",
         "Getting error code 502 from the payment gateway. Can someone confirm if this is a known issue?", "current"),
        ("t005", "entb_na_005", "prod_b", "na", "2026-08-25", "payment_failure",
         "Payment declined again. We've had to manually re-run this transaction three times this week.", "current"),
        ("t006", "entb_na_007", "prod_b", "na", "2026-08-25", "payment_failure",
         "Our card is valid but every charge attempt through Product B is failing at checkout.", "current"),
        ("t007", "entb_na_008", "prod_b", "na", "2026-08-26", "payment_failure",
         "Payment failure is now affecting our ability to renew seats. Please escalate.", "current"),
        ("t008", "entb_na_010", "prod_b", "na", "2026-08-26", "payment_failure",
         "This is the second week in a row our payment has failed. Considering switching providers if not resolved.", "current"),
        ("t009", "entb_eu_002", "prod_b", "eu", "2026-08-24", "pricing",
         "The new pricing tier increase caught us off guard mid-contract. Can we discuss options?", "current"),
        ("t010", "entb_eu_004", "prod_b", "eu", "2026-08-25", "pricing",
         "Pricing changes on Product B are higher than what was quoted during renewal.", "current"),
        ("t011", "smb_na_003", "prod_b", "na", "2026-08-23", "delivery_delay",
         "Shipment for our Product B hardware add-on is running two weeks behind schedule.", "current"),
        ("t012", "smb_na_005", "prod_b", "na", "2026-08-24", "delivery_delay",
         "Still waiting on the delayed delivery. This is affecting our own customer commitments.", "current"),
        # A few baseline (previous-period, unrelated) tickets for contrast.
        ("t013", "ent_apac_001", "prod_c", "apac", "2026-08-19", "general",
         "Quick question about API rate limits on Product C.", "previous"),
        ("t014", "smb_eu_002", "prod_a", "eu", "2026-08-20", "general",
         "Requesting an extra seat license for Product A.", "previous"),
    ]
    c.executemany(
        "INSERT INTO support_tickets VALUES (?,?,?,?,?,?,?,?)", tickets
    )

    # ---- Sales calls --------------------------------------------------
    calls = [
        ("call001", "entb_na_002", "2026-08-25",
         "Customer raised concerns about repeated billing failures during the call and asked for a credit."),
        ("call002", "entb_na_006", "2026-08-26",
         "Renewal call went well overall, but customer mentioned their finance team flagged payment issues this month."),
        ("call003", "entb_eu_003", "2026-08-24",
         "Discussed the pricing change; customer is evaluating a competitor as a result."),
    ]
    c.executemany("INSERT INTO sales_calls VALUES (?,?,?,?)", calls)

    # ---- Slack messages -------------------------------------------
    slack = [
        ("s001", "#eng-payments", "priya.eng", "2026-08-23T09:14",
         "Seeing a spike in payment gateway errors since last night's deploy. Investigating."),
        ("s002", "#eng-payments", "david.eng", "2026-08-23T10:02",
         "Confirmed - failure rate on the payment API jumped from ~1.2% to over 8% after the 08-22 release."),
        ("s003", "#cs-escalations", "maria.support", "2026-08-24T14:20",
         "Getting a wave of tickets from NA Enterprise accounts on Product B, all payment related. Escalating to eng."),
        ("s004", "#eng-payments", "david.eng", "2026-08-25T11:40",
         "Root cause identified: a bad config change to the retry/timeout handler in the payment service. Rolling back now."),
        ("s005", "#exec-updates", "priya.eng", "2026-08-26T08:00",
         "Payment API rollback deployed to staging, targeting prod fix by EOD if validation passes."),
    ]
    c.executemany("INSERT INTO slack_messages VALUES (?,?,?,?,?)", slack)

    # ---- Engineering incidents --------------------------------------
    incidents = [
        ("inc001", "payment-api", "2026-08-22",
         "Deploy on 08-22 introduced a misconfigured retry/timeout handler in the payment service, "
         "causing a sharp rise in transaction failures for Product B customers, concentrated in "
         "North America due to regional routing to the affected service cluster.",
         1.2, 8.7, "root_cause_identified"),
    ]
    c.executemany("INSERT INTO engineering_incidents VALUES (?,?,?,?,?,?,?)", incidents)

    # ---- Daily Active Users (Operational KPI, daily grain) -----------------
    # Represents the DAU / MAU Engagement KPI.
    # Previous period: Aug 15-21 (7 days).  Current period: Aug 22-29 (8 days).
    # We seed a representative set of customers covering the full period.
    # Customers on Product B / NA show a slight engagement dip during Aug 22-26
    # (correlated with the payment incident; users hitting failed checkouts
    #  quit sessions early).  All other clusters remain flat or grow slightly.
    #
    # Schema: (customer_id, date, active_minutes, session_count)
    dau_rows = []

    # --- Previous period baseline (Aug 15-21) ---
    prev_dau_dates = [
        "2026-08-15", "2026-08-16", "2026-08-17", "2026-08-18",
        "2026-08-19", "2026-08-20", "2026-08-21",
    ]
    # Baseline engagement parameters per segment (active_minutes, sessions)
    dau_baseline = {
        "entb_na_":  (52.0, 8),   # Enterprise NA Prod-B (affected cluster)
        "smb_na_":   (38.0, 5),
        "entb_eu_":  (46.0, 7),
        "ent_na_a":  (55.0, 9),
        "smb_eu_":   (34.0, 5),
        "ent_apac_": (48.0, 7),
        "smb_apac_": (31.0, 4),
    }
    for cid, name, segment, region, product in customers:
        prefix = next(p for p in dau_baseline if cid.startswith(p))
        base_mins, base_sess = dau_baseline[prefix]
        # Introduce small deterministic daily variation (±5%) keyed on date
        for d_idx, date in enumerate(prev_dau_dates):
            variation = 1.0 + (((ord(cid[-1]) + d_idx) % 5) - 2) * 0.01
            dau_rows.append((
                cid,
                date,
                round(base_mins * variation, 1),
                base_sess,
            ))

    # --- Current period (Aug 22-29) ---
    curr_dau_dates = [
        "2026-08-22", "2026-08-23", "2026-08-24", "2026-08-25",
        "2026-08-26", "2026-08-27", "2026-08-28", "2026-08-29",
    ]
    # During the incident window (Aug 22-26) the entb_na_ cluster shows
    # a measurable engagement drop (~18-22%) — users abandoning failed checkout
    # flows.  After Aug 26 (rollback deployed) engagement recovers.
    incident_dates = {"2026-08-22", "2026-08-23", "2026-08-24", "2026-08-25", "2026-08-26"}
    for cid, name, segment, region, product in customers:
        prefix = next(p for p in dau_baseline if cid.startswith(p))
        base_mins, base_sess = dau_baseline[prefix]
        for d_idx, date in enumerate(curr_dau_dates):
            variation = 1.0 + (((ord(cid[-1]) + d_idx + 3) % 5) - 2) * 0.01
            if prefix == "entb_na_" and date in incident_dates:
                # Incident-driven engagement dip: -20% on active minutes,
                # -2 sessions (users abandoning failed payment flows).
                mins = round(base_mins * variation * 0.80, 1)
                sess = max(1, base_sess - 2)
            else:
                mins = round(base_mins * variation, 1)
                sess = base_sess
            dau_rows.append((cid, date, mins, sess))

    c.executemany(
        "INSERT INTO daily_active_users (customer_id, date, active_minutes, session_count) "
        "VALUES (?,?,?,?)",
        dau_rows,
    )

    # ---- Gateway Latency Logs (Telemetry KPI, hourly grain) ----------------
    # Covers the payment-api service for the full Aug 15-29 window at hourly
    # granularity.  Two KPIs are embedded:
    #   • Gateway P99 Latency  — baseline ~210 ms; spikes to 1400-1900 ms
    #                            during the incident (Aug 22 00:00 – Aug 26 23:00).
    #   • Payment Error Rate   — baseline ~1.0-1.2%; jumps to 8.7-11.5%
    #                            during the incident window.
    # After Aug 26 23:00 (rollback confirmed) metrics return to baseline.
    #
    # Schema: (service, hour_timestamp, p99_latency_ms, error_count, total_requests)
    gateway_rows = []
    from datetime import datetime, timedelta

    SERVICE = "payment-api"
    # ~1 000 requests / hour at baseline
    BASELINE_REQUESTS = 1000
    BASELINE_P99      = 210.0   # ms
    BASELINE_ERR_RATE = 0.012   # 1.2 %

    # Incident window: Aug 22 00:00 – Aug 26 23:00 inclusive.
    incident_start = datetime(2026, 8, 22, 0)
    incident_end   = datetime(2026, 8, 26, 23)

    window_start = datetime(2026, 8, 15, 0)
    window_end   = datetime(2026, 8, 29, 23)

    ts = window_start
    while ts <= window_end:
        ts_str = ts.strftime("%Y-%m-%dT%H:00")
        in_incident = incident_start <= ts <= incident_end

        # Deterministic variation: use (day + hour) as a stable hash seed
        jitter_key = (ts.day + ts.hour) % 7  # 0-6
        if in_incident:
            # Severity ramps up steeply on Aug 22-23, peaks Aug 23-25, starts
            # recovering Aug 26 as rollback is in progress.
            days_in  = (ts - incident_start).total_seconds() / 86400  # 0-4
            if days_in < 1:          # Aug 22 — onset
                severity = 0.55 + 0.05 * jitter_key
            elif days_in < 2:        # Aug 23 — escalating
                severity = 0.70 + 0.04 * jitter_key
            elif days_in < 3:        # Aug 24 — peak
                severity = 0.85 + 0.03 * jitter_key
            elif days_in < 4:        # Aug 25 — sustained peak
                severity = 0.80 + 0.04 * jitter_key
            else:                    # Aug 26 — rollback in progress, recovering
                severity = 0.50 + 0.06 * jitter_key

            p99  = round(BASELINE_P99 + severity * 1700, 1)   # 1400-1900 ms
            err_rate = BASELINE_ERR_RATE + severity * 0.12    # 8.7 – 11.5 %
            reqs = BASELINE_REQUESTS + jitter_key * 30        # slight traffic increase
        else:
            # Quiet baseline: small deterministic jitter around 210 ms / 1.2 %
            p99      = round(BASELINE_P99 + jitter_key * 4.0, 1)
            err_rate = BASELINE_ERR_RATE + jitter_key * 0.001
            reqs     = BASELINE_REQUESTS + jitter_key * 20

        err_count = int(round(reqs * err_rate))
        gateway_rows.append((SERVICE, ts_str, p99, err_count, reqs))
        ts += timedelta(hours=1)

    c.executemany(
        "INSERT INTO gateway_latency_logs "
        "(service, hour_timestamp, p99_latency_ms, error_count, total_requests) "
        "VALUES (?,?,?,?,?)",
        gateway_rows,
    )

    # ---- Prod D sparse history (newly launched product) --------------------
    # "Product D – AI Copilot" went live on 2026-08-27.  Only 2 days of data
    # exist (<3 days) and only 4 transaction events (<5 events) are present.
    # This exercises the sparse-history branch in the analytics/AI layer.
    # Prod-D customers are all SMB / North America.
    prod_d_customers = [
        ("pd_smb_na_001", "PD SMB NA Customer 1", "SMB", "na", "prod_d"),
        ("pd_smb_na_002", "PD SMB NA Customer 2", "SMB", "na", "prod_d"),
    ]
    c.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", prod_d_customers)

    # Only 4 transactions across 2 customers over 2 days — intentionally sparse.
    prod_d_txns = [
        # (id auto) customer_id, product_id, region_id, period, date, amount, failed
        (None, "pd_smb_na_001", "prod_d", "na", "current", "2026-08-27", 4500.00, 0),
        (None, "pd_smb_na_001", "prod_d", "na", "current", "2026-08-28", 4500.00, 0),
        (None, "pd_smb_na_002", "prod_d", "na", "current", "2026-08-27", 3800.00, 0),
        (None, "pd_smb_na_002", "prod_d", "na", "current", "2026-08-28", 3800.00, 0),
    ]
    c.executemany(
        "INSERT INTO transactions (customer_id, product_id, region_id, period, date, amount, failed) "
        "VALUES (?,?,?,?,?,?,?)",
        [(r[1], r[2], r[3], r[4], r[5], r[6], r[7]) for r in prod_d_txns],
    )
    # Also add 2 DAU rows for prod_d — again intentionally sparse (<3 days).
    c.executemany(
        "INSERT INTO daily_active_users (customer_id, date, active_minutes, session_count) "
        "VALUES (?,?,?,?)",
        [
            ("pd_smb_na_001", "2026-08-27", 22.5, 3),
            ("pd_smb_na_001", "2026-08-28", 19.0, 2),
            ("pd_smb_na_002", "2026-08-27", 18.0, 2),
        ],
    )

    conn.commit()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
