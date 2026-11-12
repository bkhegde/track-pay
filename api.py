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
import webbrowser
from datetime import datetime
from urllib.parse import quote

from bank_parser import parse_statement_file
from db import Database

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
EPS = 0.005   # money tolerance
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


def _name_words(name):
    return re.findall(r"[a-z0-9]+", (name or "").lower())


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
    def set_monthly_fees(self, ids, fee):
        """Change the monthly fee of the given customers (all customers when ids is empty).
        Only invoices generated afterwards use it; existing invoices keep their amount."""
        fee = _num(fee, "Monthly fee")
        ids = [int(i) for i in (ids or [])]
        with self._db.tx() as c:
            if ids:
                cur = c.execute(f"UPDATE customers SET monthly_fee=? WHERE id IN ({_marks(len(ids))})", [fee, *ids])
            else:
                cur = c.execute("UPDATE customers SET monthly_fee=?", (fee,))
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
                if (c.execute("SELECT 1 FROM invoices WHERE customer_id=? LIMIT 1", (cid,)).fetchone()
                        or c.execute("SELECT 1 FROM transactions WHERE customer_id=? LIMIT 1", (cid,)).fetchone()):
                    skipped.append(row["name"])  # keep billing history; deactivate instead
                    continue
                c.execute("DELETE FROM customers WHERE id=?", (cid,))
                deleted += 1
        return {"deleted": deleted, "skipped": skipped}

    # ------------------------------------------------------------------ journal helpers
    @staticmethod
    def _post_invoice(c, invoice_id, customer_id, month, amount):
        """Keep the invoice's debit posting (negative amount) in step with the invoice amount."""
        if amount > EPS:
            cur = c.execute("UPDATE transactions SET amount=? WHERE invoice_id=? AND kind='INVOICE'",
                            (-amount, invoice_id))
            if cur.rowcount == 0:
                c.execute(
                    """INSERT INTO transactions(customer_id, txn_date, kind, amount, invoice_id, note)
                       VALUES (?,?,?,?,?,?)""",
                    (customer_id, month + "-01", "INVOICE", -amount, invoice_id, f"Invoice {month}"))
        else:
            c.execute("DELETE FROM transactions WHERE invoice_id=? AND kind='INVOICE'", (invoice_id,))

    @staticmethod
    def _post_payment(c, customer_id, date, kind, amount, invoice_id=None, bank_entry_id=None,
                      ref=None, note=None):
        """Post a payment (positive amount) to the journal."""
        return c.execute(
            """INSERT INTO transactions
               (customer_id, txn_date, kind, amount, invoice_id, bank_entry_id, txn_ref, note)
               VALUES (?,?,?,?,?,?,?,?)""",
            (customer_id, date, kind, amount, invoice_id, bank_entry_id, ref, note)).lastrowid

    @staticmethod
    def _allocate(c, customer_ids=None):
        """Derive each invoice's paid amount from the journal.

        A payment aimed at a specific invoice settles that invoice first; whatever is left of every
        payment then settles the customer's invoices oldest month first. Returns
        (invoices, credit): invoice id -> {customer_id, month, amount, paid, date, refs, kinds} and
        customer id -> payment not applied to any invoice (advance).
        """
        scope, params = "", []
        if customer_ids is not None:
            scope, params = f" AND customer_id IN ({_marks(len(customer_ids))})", list(customer_ids)
        invoices, by_customer = {}, {}
        for r in c.execute(f"SELECT id, customer_id, month, amount FROM invoices WHERE 1=1{scope} "
                           "ORDER BY month, id", params):
            invoices[r["id"]] = {"customer_id": r["customer_id"], "month": r["month"],
                                 "amount": r["amount"], "paid": 0.0, "date": None, "refs": [], "kinds": set()}
            by_customer.setdefault(r["customer_id"], [])
        payments = {}
        for r in c.execute(
                f"""SELECT id, customer_id, txn_date, kind, amount, invoice_id, txn_ref
                    FROM transactions WHERE kind <> 'INVOICE'{scope} ORDER BY txn_date, id""", params):
            payments.setdefault(r["customer_id"], []).append(
                {"left": r["amount"], "date": r["txn_date"], "kind": r["kind"],
                 "invoice_id": r["invoice_id"], "ref": r["txn_ref"]})
        for iid, inv in invoices.items():
            by_customer[inv["customer_id"]].append(iid)

        def apply(inv, pay, take):
            inv["paid"] += take
            pay["left"] -= take
            inv["date"] = max(inv["date"] or "", pay["date"])
            if pay["ref"] and pay["ref"] not in inv["refs"]:
                inv["refs"].append(pay["ref"])
            inv["kinds"].add(pay["kind"])

        credit = {}
        for cid in set(by_customer) | set(payments):
            pays = payments.get(cid, [])
            invs = [invoices[i] for i in by_customer.get(cid, [])]
            for pay in pays:                               # pass 1: payments aimed at an invoice
                inv = invoices.get(pay["invoice_id"])
                if inv and inv["customer_id"] == cid:
                    take = min(pay["left"], inv["amount"] - inv["paid"])
                    if take > EPS:
                        apply(inv, pay, take)
            for inv in invs:                               # pass 2: oldest invoice first
                for pay in pays:
                    need = inv["amount"] - inv["paid"]
                    if need <= EPS:
                        break
                    take = min(pay["left"], need)
                    if take > EPS:
                        apply(inv, pay, take)
            credit[cid] = round(sum(p["left"] for p in pays), 2)
        for inv in invoices.values():
            inv["paid"] = round(inv["paid"], 2)
        return invoices, credit

    @staticmethod
    def _status(amount, paid):
        if paid > EPS and amount - paid <= EPS:
            return "PAID"
        return "PARTIAL" if paid > EPS else "UNPAID"

    # ------------------------------------------------------------------ invoices
    @safe
    def generate_invoices(self, month):
        """Create the month's missing invoices and post each one to the journal as a debit."""
        _month(month)
        with self._db.tx() as c:
            active = c.execute("SELECT COUNT(*) FROM customers WHERE active=1").fetchone()[0]
            cur = c.execute(
                """INSERT OR IGNORE INTO invoices(customer_id, month, amount)
                   SELECT id, ?, monthly_fee FROM customers WHERE active=1""", (month,))
            created = cur.rowcount
            c.execute(
                """INSERT INTO transactions(customer_id, txn_date, kind, amount, invoice_id, note)
                   SELECT customer_id, month || '-01', 'INVOICE', -amount, id, 'Invoice ' || month
                   FROM invoices
                   WHERE month=? AND amount > 0
                     AND id NOT IN (SELECT invoice_id FROM transactions WHERE kind='INVOICE')""", (month,))
        return {"created": created, "existing": active - created}

    @safe
    def list_invoices(self, month=None, status="ALL"):
        """status: ALL, PAID (fully paid) or UNPAID (anything still outstanding, partly paid included)."""
        sql = """SELECT i.id, i.customer_id, c.name AS customer_name, c.phone, i.month, i.amount,
                        COALESCE((SELECT group_concat(upi_id, ', ')
                                  FROM customer_upi u WHERE u.customer_id = c.id), '') AS upi_ids
                 FROM invoices i JOIN customers c ON c.id = i.customer_id WHERE 1=1"""
        params = []
        if month and month != "ALL":
            sql += " AND i.month=?"
            params.append(_month(month))
        sql += " ORDER BY i.month, c.name COLLATE NOCASE"
        with self._db.tx() as c:
            rows = [dict(r) for r in c.execute(sql, params).fetchall()]
            alloc, _ = self._allocate(c, {r["customer_id"] for r in rows})
        out = []
        for r in rows:
            a = alloc[r["id"]]
            r["status"] = self._status(r["amount"], a["paid"])
            if status == "PAID" and r["status"] != "PAID":
                continue
            if status == "UNPAID" and r["status"] == "PAID":
                continue
            r["paid_amount"] = a["paid"] or None
            r["balance"] = round(max(r["amount"] - a["paid"], 0), 2)
            r["paid_date"] = a["date"]
            r["txn_id"] = ", ".join(a["refs"])
            r["paid_via"] = " + ".join(n for k, n in (("BANK", "Bank"), ("CASH", "Cash")) if k in a["kinds"])
            out.append(r)
        return out

    def _set_amounts(self, c, where, params, amount):
        rows = c.execute(f"SELECT id, customer_id, month FROM invoices WHERE {where}", params).fetchall()
        alloc, _ = self._allocate(c, {r["customer_id"] for r in rows})
        updated = 0
        for r in rows:
            if self._status(alloc[r["id"]]["amount"], alloc[r["id"]]["paid"]) == "PAID":
                continue   # fully paid invoices are history
            c.execute("UPDATE invoices SET amount=? WHERE id=?", (amount, r["id"]))
            self._post_invoice(c, r["id"], r["customer_id"], r["month"], amount)
            updated += 1
        return {"updated": updated}

    @safe
    def update_invoice_amounts(self, ids, amount):
        ids, amount = _ids(ids), _num(amount, "Amount")
        with self._db.tx() as c:
            return self._set_amounts(c, f"id IN ({_marks(len(ids))})", ids, amount)

    @safe
    def update_month_amounts(self, month, amount):
        _month(month)
        amount = _num(amount, "Amount")
        with self._db.tx() as c:
            return self._set_amounts(c, "month=?", [month], amount)

    # ------------------------------------------------------------------ payments & ledger
    @safe
    def add_cash_payment(self, customer_id, amount, payment_date=None, note=None, invoice_id=None):
        """Post a cash payment (positive amount) to the customer's account.

        It settles `invoice_id` first when given, then the oldest outstanding invoices."""
        amount = _num(amount, "Amount")
        if amount <= 0:
            raise ValueError("Amount must be greater than zero")
        date = _date(payment_date) or datetime.now().strftime("%Y-%m-%d")
        with self._db.tx() as c:
            if not c.execute("SELECT 1 FROM customers WHERE id=?", (customer_id,)).fetchone():
                raise ValueError("Customer not found")
            if invoice_id and not c.execute("SELECT 1 FROM invoices WHERE id=? AND customer_id=?",
                                            (invoice_id, customer_id)).fetchone():
                raise ValueError("That invoice does not belong to this customer")
            tid = self._post_payment(c, customer_id, date, "CASH", amount, invoice_id or None,
                                     note=(note or "").strip() or None)
        return {"id": tid}

    @safe
    def delete_payments(self, ids):
        """Remove payment postings (cash or bank). A removed bank payment frees its bank entry."""
        ids = _ids(ids)
        with self._db.tx() as c:
            cur = c.execute(f"DELETE FROM transactions WHERE kind <> 'INVOICE' AND id IN ({_marks(len(ids))})", ids)
        return {"updated": cur.rowcount}

    @safe
    def list_transactions(self, customer_id=None):
        """Journal rows, newest first, with each customer's running balance (negative = owes)."""
        with self._db.tx() as c:
            rows = [dict(r) for r in c.execute(
                """SELECT t.id, t.customer_id, c.name AS customer_name, t.txn_date, t.kind, t.amount,
                          t.txn_ref, i.month AS invoice_month,
                          COALESCE(t.note, b.description, '') AS description
                   FROM transactions t
                   JOIN customers c ON c.id = t.customer_id
                   LEFT JOIN invoices i ON i.id = t.invoice_id
                   LEFT JOIN bank_entries b ON b.id = t.bank_entry_id
                   ORDER BY t.txn_date, t.id""").fetchall()]
        running = {}
        for r in rows:
            running[r["customer_id"]] = round(running.get(r["customer_id"], 0) + r["amount"], 2)
            r["balance"] = running[r["customer_id"]]
        if customer_id:
            rows = [r for r in rows if r["customer_id"] == int(customer_id)]
        rows.reverse()
        return rows

    # ------------------------------------------------------------------ reminders
    @safe
    def open_whatsapp(self, phone, message):
        """Open a WhatsApp chat with `message` prefilled (click-to-chat); the user presses Send."""
        digits = re.sub(r"\D", "", phone or "")
        if len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        if len(digits) == 10:
            digits = "91" + digits  # bare Indian mobile number
        if not 11 <= len(digits) <= 15:
            raise ValueError("Phone number is missing or invalid")
        webbrowser.open(f"https://wa.me/{digits}?text={quote(message or '')}")
        return True

    # ------------------------------------------------------------------ backup
    @safe
    def backup_database(self):
        return {"path": str(self._db.backup_now())}

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
            "UNMATCHED_CREDITS": "WHERE b.credit > 0 AND t.id IS NULL",
            "MATCHED": "WHERE t.id IS NOT NULL",
        }.get(view, "")
        with self._db.tx() as c:
            rows = c.execute(
                f"""SELECT b.id, b.txn_date, b.description, b.txn_ref, b.debit, b.credit, b.balance,
                           t.customer_id AS matched_customer_id,
                           COALESCE(c.name, '') AS matched_to
                    FROM bank_entries b
                    LEFT JOIN transactions t ON t.bank_entry_id = b.id
                    LEFT JOIN customers c ON c.id = t.customer_id
                    {where} ORDER BY b.txn_date DESC, b.id DESC""").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ reconciliation
    @staticmethod
    def _link(c, customer_id, entry, invoice_id=None):
        """Post a bank credit to the journal as a payment (positive amount) for the customer."""
        Api._post_payment(c, customer_id, entry["txn_date"], "BANK", entry["credit"], invoice_id,
                          bank_entry_id=entry["id"], ref=entry["txn_ref"] or f"BANK-{entry['id']}")

    @staticmethod
    def _learn_upi(c, customer_id, description):
        """Remember the payer's UPI id from a manually matched narration so later months auto-match.

        The statement shows only the last 4 chars before '@' plus the handle (e.g. ******1729@OKICICI).
        Returns the id saved, or None if there was none / it is already known."""
        m = re.search(r"\*([a-z0-9._\-]{2,})@([a-z][a-z0-9]{1,63})(?![a-z0-9])", (description or "").lower())
        if not m:
            m = re.search(r"(?<![a-z0-9._\-])([a-z0-9._\-]{2,})@([a-z][a-z0-9]{1,63})(?![a-z0-9])",
                          (description or "").lower())
            if not m:
                return None
        upi = m.group(1)[-4:] + "@" + m.group(2)
        if not UPI_RE.match(upi):
            return None
        cur = c.execute("INSERT OR IGNORE INTO customer_upi(customer_id, upi_id) VALUES (?,?)", (customer_id, upi))
        return upi if cur.rowcount else None   # already used (by this or another customer): leave as is

    @safe
    def auto_reconcile(self, month=None):
        """Post unmatched bank credits as payments to the customer found in the narration.

        The customer is identified by name AND UPI id (last 4 chars before '@' + handle); if the narration
        has no name, the UPI id alone is used. The customer must have an outstanding invoice (in `month`
        when given). The amount may be partial, or cover several invoices: payments settle the oldest
        invoice first, and any excess stays on the account as an advance."""
        month = _month(month) if month and month != "ALL" else None
        result = {"matched": 0, "no_match": 0, "ambiguous": 0, "no_invoice": 0, "details": []}
        with self._db.tx() as c:
            names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM customers")}
            upis = {}
            for r in c.execute("SELECT upi_id, customer_id FROM customer_upi"):
                local, _, handle = r["upi_id"].partition("@")
                # the statement shows only the last four characters before '@', then the full handle
                upis.setdefault(r["customer_id"], []).append(
                    re.compile(re.escape(local[-4:] + "@" + handle) + r"(?![a-z0-9])"))
            patterns = []
            for cid, name in names.items():
                words = _name_words(name)
                if words and cid in upis:
                    patterns.append((re.compile(r"(?<![a-z0-9])" + r"[^a-z0-9]+".join(map(re.escape, words))
                                                + r"(?![a-z0-9])"), upis[cid], cid, len(words)))
            entries = c.execute(
                """SELECT * FROM bank_entries b WHERE credit > 0
                   AND NOT EXISTS (SELECT 1 FROM transactions t WHERE t.bank_entry_id = b.id)
                   ORDER BY txn_date, id""").fetchall()
            for e in entries:
                desc = (e["description"] or "").lower()
                hits = [(cid, n) for pat, upi_pats, cid, n in patterns
                        if pat.search(desc) and any(u.search(desc) for u in upi_pats)]
                # "Ravi" also appears in "Ravi Kumar": only the most specific (longest) names count
                best = max((n for _, n in hits), default=0)
                owners = {cid for cid, n in hits if n == best}
                if not owners:
                    # no name in the narration: fall back to the UPI id alone (it repeats on every payment)
                    owners = {cid for cid, upi_pats in upis.items() if any(u.search(desc) for u in upi_pats)}
                if not owners:
                    result["no_match"] += 1
                    continue
                if len(owners) > 1:
                    result["ambiguous"] += 1
                    continue
                cid = owners.pop()
                invoices, _ = self._allocate(c, [cid])   # current state, including payments posted this run
                owed = [i for i in invoices.values()
                        if i["amount"] - i["paid"] > EPS and (not month or i["month"] == month)]
                if not owed:
                    result["no_invoice"] += 1
                    result["details"].append(
                        f"{e['txn_date']}: {e['credit']:.2f} from {names[cid]} has no outstanding invoice"
                        + (f" for {month}" if month else ""))
                    continue
                outstanding = sum(i["amount"] - i["paid"] for i in owed)
                self._link(c, cid, e)
                result["matched"] += 1
                note = ""
                if e["credit"] < outstanding - EPS:
                    note = f" (partial, {outstanding - e['credit']:.2f} still due)"
                elif e["credit"] > outstanding + EPS:
                    note = f" (excess {e['credit'] - outstanding:.2f} kept as advance)"
                result["details"].append(
                    f"{e['txn_date']}: {e['credit']:.2f} posted to {names[cid]}{note}")
        return result

    @safe
    def manual_match(self, invoice_id, bank_entry_id):
        """Post a bank credit as a payment for the invoice's customer; the invoice is settled first.
        The payer's UPI id in the narration is saved on the customer so auto reconcile catches it next time."""
        with self._db.tx() as c:
            inv = c.execute("SELECT customer_id FROM invoices WHERE id=?", (invoice_id,)).fetchone()
            entry = c.execute("SELECT * FROM bank_entries WHERE id=?", (bank_entry_id,)).fetchone()
            if not inv or not entry:
                raise ValueError("Invoice or bank entry not found")
            if c.execute("SELECT 1 FROM transactions WHERE bank_entry_id=?", (bank_entry_id,)).fetchone():
                raise ValueError("This bank entry is already matched")
            if entry["credit"] <= 0:
                raise ValueError("Only credit entries can be matched to an invoice")
            invoices, _ = self._allocate(c, [inv["customer_id"]])
            if invoices[invoice_id]["amount"] - invoices[invoice_id]["paid"] <= EPS:
                raise ValueError("This invoice is already paid")
            self._link(c, inv["customer_id"], entry, invoice_id)
            learned = self._learn_upi(c, inv["customer_id"], entry["description"])
        return {"learned": learned}
