"""SQLite persistence for Track-Pay."""
import os
import shutil
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
BACKUP_INTERVAL_DAYS = 7
BACKUP_KEEP = 12          # weekly backups retained (~3 months)

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
    folder = BASE / "data"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "trackpay.db"
    legacy = Path.home() / ".trackpay" / "trackpay.db"
    if not path.exists() and legacy.exists():   # one-time move from the old location
        shutil.copy2(legacy, path)
    return path


def default_backup_dir() -> Path:
    override = os.environ.get("TRACKPAY_BACKUP_DIR")
    return Path(override) if override else BASE / "backups"


class Database:
    """A new connection is opened per call (pywebview calls the API from worker threads)."""

    def __init__(self, path=None):
        self.path = str(path or default_db_path())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.tx() as conn:
            conn.executescript(SCHEMA)
        self.backup_dir = default_backup_dir()
        self.backup_if_due()

    # ---- backups -------------------------------------------------------------------
    def backup_now(self):
        """Snapshot the database into the backup folder; returns the new file's path."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(self.path).stem
        target = self.backup_dir / f"{stem}-{datetime.now():%Y-%m-%d_%H%M%S}.db"
        src = sqlite3.connect(self.path)
        try:
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)   # consistent snapshot even while the DB is in use
            finally:
                dst.close()
        finally:
            src.close()
        for old in sorted(self.backup_dir.glob(f"{stem}-*.db"))[:-BACKUP_KEEP]:
            old.unlink(missing_ok=True)
        return target

    def backup_if_due(self):
        """Weekly automatic backup at startup. Never raises."""
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            existing = sorted(self.backup_dir.glob(f"{Path(self.path).stem}-*.db"))
            if existing and time.time() - existing[-1].stat().st_mtime < BACKUP_INTERVAL_DAYS * 86400:
                return
            self.backup_now()
        except Exception as e:  # a failed backup must never stop the app
            print(f"Backup failed: {e}")

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
