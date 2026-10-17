"""SQLite persistence for Track-Pay."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    phone        TEXT,
    monthly_fee  REAL    NOT NULL DEFAULT 0,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS customer_upi (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id  INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    upi_id       TEXT    NOT NULL UNIQUE          -- stored lower-case
);

CREATE TABLE IF NOT EXISTS bank_entries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_hash          TEXT    NOT NULL UNIQUE,  -- idempotency key
    txn_date            TEXT    NOT NULL,         -- YYYY-MM-DD
    description         TEXT,
    txn_ref             TEXT,
    debit               REAL    NOT NULL DEFAULT 0,
    credit              REAL    NOT NULL DEFAULT 0,
    balance             REAL,
    source_file         TEXT,
    matched_invoice_id  INTEGER,
    created_at          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS invoices (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id    INTEGER NOT NULL REFERENCES customers(id),
    month          TEXT    NOT NULL,              -- YYYY-MM
    amount         REAL    NOT NULL,
    status         TEXT    NOT NULL DEFAULT 'UNPAID' CHECK (status IN ('UNPAID','PAID')),
    paid_amount    REAL,
    paid_date      TEXT,
    txn_id         TEXT,
    bank_entry_id  INTEGER REFERENCES bank_entries(id),
    created_at     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (customer_id, month)                   -- idempotent invoice generation
);

CREATE INDEX IF NOT EXISTS idx_invoices_month  ON invoices(month, status);
CREATE INDEX IF NOT EXISTS idx_bank_unmatched  ON bank_entries(matched_invoice_id);
"""


def default_db_path() -> Path:
    override = os.environ.get("TRACKPAY_DB")
    if override:
        return Path(override)
    folder = Path.home() / ".trackpay"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "trackpay.db"


class Database:
    """A new connection is opened per call (pywebview calls the API from worker threads)."""

    def __init__(self, path=None):
        self.path = str(path or default_db_path())
        with self.tx() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def tx(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
