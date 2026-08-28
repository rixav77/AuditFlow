"""SQLite schema for generated batches."""

DDL = [
    """
    CREATE TABLE batch_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE orders (
        order_id      TEXT PRIMARY KEY,
        amount_paise  INTEGER NOT NULL,
        customer_name TEXT NOT NULL,
        item_desc     TEXT NOT NULL,
        status        TEXT NOT NULL,
        created_at    TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE payments (
        payment_id    TEXT PRIMARY KEY,
        order_id      TEXT,
        processor_ref TEXT,
        amount_paise  INTEGER,
        method        TEXT NOT NULL,
        status        TEXT NOT NULL,
        paid_at       TEXT
    )
    """,
    """
    CREATE TABLE settlements (
        settlement_id TEXT PRIMARY KEY,
        payment_id    TEXT,
        processor_ref TEXT,
        gross_paise   INTEGER NOT NULL,
        fee_paise     INTEGER NOT NULL,
        tax_paise     INTEGER NOT NULL DEFAULT 0,
        net_paise     INTEGER NOT NULL,
        utr           TEXT,
        settled_at    TEXT
    )
    """,
    """
    CREATE TABLE bank_txns (
        bank_txn_id  TEXT PRIMARY KEY,
        narration    TEXT NOT NULL,
        amount_paise INTEGER NOT NULL,
        posted_at    TEXT NOT NULL,
        value_date   TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE adjustments (
        adjustment_id TEXT PRIMARY KEY,
        adj_type      TEXT NOT NULL,
        payment_id    TEXT NOT NULL,
        amount_paise  INTEGER NOT NULL,
        created_at    TEXT NOT NULL,
        reason        TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE audit_events (
        event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        work_key     TEXT,
        stage        TEXT NOT NULL,
        event        TEXT NOT NULL,
        payload_json TEXT
    )
    """,
]

INDICES = [
    "CREATE INDEX idx_payments_order ON payments(order_id)",
    "CREATE INDEX idx_payments_proc ON payments(processor_ref)",
    "CREATE INDEX idx_payments_amount ON payments(amount_paise)",
    "CREATE INDEX idx_settle_payment ON settlements(payment_id)",
    "CREATE INDEX idx_settle_proc ON settlements(processor_ref)",
    "CREATE INDEX idx_settle_utr ON settlements(utr)",
    "CREATE INDEX idx_bank_amount ON bank_txns(amount_paise)",
    "CREATE INDEX idx_bank_posted ON bank_txns(posted_at)",
    "CREATE INDEX idx_adj_payment ON adjustments(payment_id)",
    "CREATE INDEX idx_audit_workkey ON audit_events(work_key)",
]
