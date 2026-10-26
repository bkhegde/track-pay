"""Business logic. An instance of Api is exposed to the Svelte UI through pywebview's js_api.

Every public method returns {"data": ...} or {"error": "message"}.
Internal state is underscore-prefixed so pywebview does not expose it to JavaScript.
"""
import base64
import binascii
import functools
import re
import sqlite3
import traceback
from datetime import datetime

from bank_parser import parse_statement_file
from db import Database

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
UPI_RE = re.compile(r"^[a-z0-9._\-]{2,256}@[a-z][a-z0-9]{1,63}$")


def safe(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return {"data": fn(self, *args, **kwargs)}
        except ValueError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return {"error": f"Unexpected error: {exc}"}
    return wrapper


def _month(value):
    if not value or not MONTH_RE.match(value):
        raise ValueError("Month must be in YYYY-MM format")
    return value


def _date(value):
    if value in (None, ""):
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except (ValueError, TypeError):
        raise ValueError("Date must be in YYYY-MM-DD format")
    return value


def _num(value, label, optional=False):
    if value in (None, ""):
        if optional:
            return None
        raise ValueError(f"{label} is required")
    try:
        number = round(float(value), 2)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number")
    if number < 0:
        raise ValueError(f"{label} cannot be negative")
    return number


def _ids(ids):
    ids = [int(i) for i in (ids or [])]
    if not ids:
        raise ValueError("Nothing is selected")
    return ids


def _marks(n):
    return ",".join("?" * n)


def _parse_upis(value):
    items = value if isinstance(value, list) else re.split(r"[,;\s]+", value or "")
    result = []
    for item in items:
        item = item.strip().lower()
        if not item or item in result:
            continue
        if not UPI_RE.match(item):
            raise ValueError(f"'{item}' is not a valid UPI id (expected name@bank)")
        result.append(item)
    return result


class Api:
    def __init__(self, db_path=None):
        self._db = Database(db_path)

    # ------------------------------------------------------------------ customers
    @safe
    def list_customers(self):
        with self._db.tx() as c:
            rows = c.execute(
                """SELECT c.id, c.name, c.phone, c.monthly_fee, c.active,
                          COALESCE((SELECT group_concat(upi_id, ', ')
                                    FROM customer_upi u WHERE u.customer_id = c.id), '') AS upi_ids
                   FROM customers c ORDER BY c.name COLLATE NOCASE"""
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["active"] = bool(d["active"])
            out.append(d)
        return out

    @safe
    def save_customer(self, data):
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("Name is required")
        phone = (data.get("phone") or "").strip()
        if phone and not re.fullmatch(r"\+?[\d\s\-]{6,19}", phone):
            raise ValueError("Phone number looks invalid")
        fee = _num(data.get("monthly_fee", 0), "Monthly fee")
        active = 1 if data.get("active", True) else 0
        upis = _parse_upis(data.get("upi_ids"))
        cid = data.get("id")
        try:
            with self._db.tx() as c:
                if cid:
                    cur = c.execute(
                        "UPDATE customers SET name=?, phone=?, monthly_fee=?, active=? WHERE id=?",
                        (name, phone, fee, active, cid))
                    if cur.rowcount == 0:
                        raise ValueError("Customer not found")
                    c.execute("DELETE FROM customer_upi WHERE customer_id=?", (cid,))
                else:
                    cid = c.execute(
                        "INSERT INTO customers(name, phone, monthly_fee, active) VALUES (?,?,?,?)",
                        (name, phone, fee, active)).lastrowid
                c.executemany("INSERT INTO customer_upi(customer_id, upi_id) VALUES (?,?)",
                              [(cid, u) for u in upis])
        except sqlite3.IntegrityError:
            raise ValueError("A UPI id is already assigned to another customer")
        return {"id": cid}

    @safe
    def set_customers_active(self, ids, active):
        ids = _ids(ids)
        with self._db.tx() as c:
            cur = c.execute(f"UPDATE customers SET active=? WHERE id IN ({_marks(len(ids))})",
                            [1 if active else 0, *ids])
        return {"updated": cur.rowcount}

    @safe
    def delete_customers(self, ids):
        ids = _ids(ids)
        deleted, skipped = 0, []
        with self._db.tx() as c:
            for cid in ids:
                row = c.execute("SELECT name FROM customers WHERE id=?", (cid,)).fetchone()
                if not row:
                    continue
                if c.execute("SELECT 1 FROM invoices WHERE customer_id=? LIMIT 1", (cid,)).fetchone():
                    skipped.append(row["name"])  # keep billing history; deactivate instead
                    continue
                c.execute("DELETE FROM customers WHERE id=?", (cid,))
                deleted += 1
        return {"deleted": deleted, "skipped": skipped}

    # ------------------------------------------------------------------ invoices
    @safe
    def generate_invoices(self, month):
        _month(month)
        with self._db.tx() as c:
            active = c.execute("SELECT COUNT(*) FROM customers WHERE active=1").fetchone()[0]
            cur = c.execute(
                """INSERT OR IGNORE INTO invoices(customer_id, month, amount)
                   SELECT id, ?, monthly_fee FROM customers WHERE active=1""", (month,))
        return {"created": cur.rowcount, "existing": active - cur.rowcount}

    @safe
    def list_invoices(self, month=None, status="ALL"):
        sql = """SELECT i.id, i.customer_id, c.name AS customer_name, c.phone, i.month, i.amount,
                        i.status, i.paid_amount, i.paid_date, i.txn_id,
                        CASE WHEN i.bank_entry_id IS NOT NULL THEN 'Bank'
                             WHEN i.status='PAID' THEN 'Manual' ELSE '' END AS paid_via,
                        COALESCE((SELECT group_concat(upi_id, ', ')
                                  FROM customer_upi u WHERE u.customer_id = c.id), '') AS upi_ids
                 FROM invoices i JOIN customers c ON c.id = i.customer_id WHERE 1=1"""
        params = []
        if month and month != "ALL":
            sql += " AND i.month=?"
            params.append(_month(month))
        if status in ("UNPAID", "PAID"):
            sql += " AND i.status=?"
            params.append(status)
        sql += " ORDER BY i.month, c.name COLLATE NOCASE"
        with self._db.tx() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    @safe
    def update_invoice_amounts(self, ids, amount):
        ids, amount = _ids(ids), _num(amount, "Amount")
        with self._db.tx() as c:
            cur = c.execute(
                f"UPDATE invoices SET amount=? WHERE status='UNPAID' AND id IN ({_marks(len(ids))})",
                [amount, *ids])
        return {"updated": cur.rowcount}

    @safe
    def update_month_amounts(self, month, amount):
        _month(month)
        amount = _num(amount, "Amount")
        with self._db.tx() as c:
            cur = c.execute("UPDATE invoices SET amount=? WHERE month=? AND status='UNPAID'",
                            (amount, month))
        return {"updated": cur.rowcount}

    @staticmethod
    def _mark_paid(c, invoice_id, amount, date):
        row = c.execute("SELECT amount FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        if not row:
            raise ValueError("Invoice not found")
        paid = amount if amount is not None else row["amount"]
        c.execute("UPDATE invoices SET status='PAID', paid_amount=?, paid_date=? WHERE id=?",
                  (paid, date, invoice_id))

    @staticmethod
    def _mark_unpaid(c, invoice_id):
        c.execute("UPDATE bank_entries SET matched_invoice_id=NULL WHERE matched_invoice_id=?",
                  (invoice_id,))
        c.execute("""UPDATE invoices SET status='UNPAID', paid_amount=NULL, paid_date=NULL,
                     txn_id=NULL, bank_entry_id=NULL WHERE id=?""", (invoice_id,))

    @safe
    def mark_paid(self, ids, paid_amount=None, paid_date=None):
        ids = _ids(ids)
        amount, date = _num(paid_amount, "Paid amount", optional=True), _date(paid_date)
        with self._db.tx() as c:
            for i in ids:
                self._mark_paid(c, i, amount, date)
        return {"updated": len(ids)}

    @safe
    def update_payment(self, invoice_id, paid_amount, paid_date):
        """Inline grid edit: blank amount and blank date turns the invoice back to unpaid."""
        amount, date = _num(paid_amount, "Paid amount", optional=True), _date(paid_date)
        with self._db.tx() as c:
            if amount is None and date is None:
                self._mark_unpaid(c, invoice_id)
            else:
                self._mark_paid(c, invoice_id, amount, date)
        return True

    @safe
    def mark_unpaid(self, ids):
        ids = _ids(ids)
        with self._db.tx() as c:
            for i in ids:
                self._mark_unpaid(c, i)
        return {"updated": len(ids)}

    # ------------------------------------------------------------------ bank statement
    @safe
    def import_bank_statement(self, filename, content_b64):
        try:
            data = base64.b64decode(content_b64 or "", validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("The uploaded file could not be decoded")
        entries = parse_statement_file(data)
        inserted = 0
        with self._db.tx() as c:
            for e in entries:
                cur = c.execute(
                    """INSERT OR IGNORE INTO bank_entries
                       (entry_hash, txn_date, description, txn_ref, debit, credit, balance, source_file)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (e["entry_hash"], e["txn_date"], e["description"], e["txn_ref"],
                     e["debit"], e["credit"], e["balance"], filename))
                inserted += cur.rowcount
        return {"total": len(entries), "inserted": inserted, "duplicates": len(entries) - inserted}

    @safe
    def list_bank_entries(self, view="ALL"):
        where = {
            "UNMATCHED_CREDITS": "WHERE b.credit > 0 AND b.matched_invoice_id IS NULL",
            "MATCHED": "WHERE b.matched_invoice_id IS NOT NULL",
        }.get(view, "")
        with self._db.tx() as c:
            rows = c.execute(
                f"""SELECT b.id, b.txn_date, b.description, b.txn_ref, b.debit, b.credit, b.balance,
                           b.matched_invoice_id,
                           CASE WHEN b.matched_invoice_id IS NULL THEN ''
                                ELSE c.name || ' / ' || i.month END AS matched_to
                    FROM bank_entries b
                    LEFT JOIN invoices i ON i.id = b.matched_invoice_id
                    LEFT JOIN customers c ON c.id = i.customer_id
                    {where} ORDER BY b.txn_date DESC, b.id DESC""").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ reconciliation
    @staticmethod
    def _link(c, invoice_id, entry):
        c.execute(
            """UPDATE invoices SET status='PAID', paid_amount=?, paid_date=?, txn_id=?, bank_entry_id=?
               WHERE id=?""",
            (entry["credit"], entry["txn_date"], entry["txn_ref"] or f"BANK-{entry['id']}",
             entry["id"], invoice_id))
        c.execute("UPDATE bank_entries SET matched_invoice_id=? WHERE id=?", (invoice_id, entry["id"]))

    @safe
    def auto_reconcile(self, month=None):
        """Match credit entries to unpaid invoices: UPI id found in the narration + exact amount."""
        month = _month(month) if month and month != "ALL" else None
        result = {"matched": 0, "no_upi_match": 0, "ambiguous": 0, "no_invoice": 0, "details": []}
        with self._db.tx() as c:
            names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM customers")}
            patterns = [
                (re.compile(r"(?<![a-z0-9._])" + re.escape(r["upi_id"]) + r"(?![a-z0-9])"),
                 r["customer_id"])
                for r in c.execute("SELECT upi_id, customer_id FROM customer_upi")
            ]
            entries = c.execute(
                """SELECT * FROM bank_entries WHERE credit > 0 AND matched_invoice_id IS NULL
                   ORDER BY txn_date, id""").fetchall()
            for e in entries:
                desc = (e["description"] or "").lower()
                owners = {cid for pat, cid in patterns if pat.search(desc)}
                if not owners:
                    result["no_upi_match"] += 1
                    continue
                if len(owners) > 1:
                    result["ambiguous"] += 1
                    continue
                cid = owners.pop()
                sql = ("SELECT id, month FROM invoices WHERE customer_id=? AND status='UNPAID' "
                       "AND ABS(amount - ?) < 0.005")
                params = [cid, e["credit"]]
                if month:
                    sql += " AND month=?"
                    params.append(month)
                inv = c.execute(sql + " ORDER BY month LIMIT 1", params).fetchone()
                if not inv:
                    result["no_invoice"] += 1
                    result["details"].append(
                        f"{e['txn_date']}: {e['credit']:.2f} from {names[cid]} has no unpaid invoice of that amount")
                    continue
                self._link(c, inv["id"], e)
                result["matched"] += 1
                result["details"].append(
                    f"{e['txn_date']}: {e['credit']:.2f} matched to {names[cid]} / {inv['month']}")
        return result

    @safe
    def manual_match(self, invoice_id, bank_entry_id):
        with self._db.tx() as c:
            inv = c.execute("SELECT status FROM invoices WHERE id=?", (invoice_id,)).fetchone()
            entry = c.execute("SELECT * FROM bank_entries WHERE id=?", (bank_entry_id,)).fetchone()
            if not inv or not entry:
                raise ValueError("Invoice or bank entry not found")
            if inv["status"] == "PAID":
                raise ValueError("This invoice is already paid")
            if entry["matched_invoice_id"] is not None:
                raise ValueError("This bank entry is already matched")
            if entry["credit"] <= 0:
                raise ValueError("Only credit entries can be matched to an invoice")
            self._link(c, invoice_id, entry)
        return True
