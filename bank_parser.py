"""Parse bank statements (CSV text or legacy .xls workbooks) into normalised entries with stable
idempotency hashes."""
import csv
import hashlib
import io
import re
from collections import Counter
from datetime import datetime

DATE_FORMATS = [
    "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%Y-%m-%d", "%Y/%m/%d",
    "%d %b %Y", "%d-%b-%Y", "%d-%b-%y", "%d %b %y", "%d/%b/%Y", "%d.%m.%Y",
]

DATE_KEYS = ["txn date", "transaction date", "posting date", "date"]
DESC_KEYS = ["narration", "description", "particulars", "remarks", "details"]
REF_KEYS = ["transaction id", "txn id", "utr", "ref no", "reference", "chq", "cheque"]
DEBIT_KEYS = ["withdrawal", "debit"]
CREDIT_KEYS = ["deposit", "credit"]
BALANCE_KEYS = ["balance"]


def _norm(text):
    return re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).strip()


def _find(headers, keys, exclude=()):
    normed = [_norm(h) for h in headers]
    for key in keys:  # earlier keys have priority
        for i, n in enumerate(normed):
            if key in n and not any(e in n for e in exclude):
                return i
    return None


def parse_date(text):
    text = (text or "").strip()
    candidates = [text, text.split(" ")[0], text.split("T")[0]]
    for cand in candidates:
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(cand, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


def parse_amount(text):
    text = (text or "").replace(",", "").replace("\u20b9", "")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return round(float(m.group()), 2) if m else 0.0


def _locate_header(rows):
    for idx, row in enumerate(rows):
        d = _find(row, DATE_KEYS, exclude=("value",))
        db = _find(row, DEBIT_KEYS)
        cr = _find(row, CREDIT_KEYS)
        desc = _find(row, DESC_KEYS)
        if d is not None and desc is not None and (db is not None or cr is not None):
            return idx, {
                "date": d,
                "desc": desc,
                "debit": db,
                "credit": cr,
                "ref": _find(row, REF_KEYS),
                "balance": _find(row, BALANCE_KEYS),
            }
    return None, None


def _cell(row, idx):
    return row[idx].strip() if idx is not None and idx < len(row) else ""


OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy Excel 97-2003 (.xls)
ZIP_MAGIC = b"PK\x03\x04"  # .xlsx


def _decode_text(data):
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _xls_cell(cell, datemode):
    import xlrd
    if cell.ctype == xlrd.XL_CELL_DATE:
        try:
            return xlrd.xldate_as_datetime(cell.value, datemode).strftime("%Y-%m-%d")
        except (ValueError, OverflowError):
            return ""
    if cell.ctype == xlrd.XL_CELL_NUMBER:
        v = cell.value
        return str(int(v)) if v == int(v) else repr(v)
    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR):
        return ""
    return str(cell.value)


def _xls_sheets(data):
    try:
        import xlrd
    except ImportError:
        raise ValueError("Reading .xls statements needs the 'xlrd' package (pip install xlrd).")
    try:
        book = xlrd.open_workbook(file_contents=data)
    except xlrd.XLRDError as e:
        raise ValueError(f"Could not read the Excel file: {e}")
    for sheet in book.sheets():
        yield [[_xls_cell(c, book.datemode) for c in sheet.row(r)] for r in range(sheet.nrows)]


def parse_statement_file(data):
    """Parse raw statement file bytes, detecting .xls vs CSV/text from the content."""
    if data.startswith(OLE2_MAGIC):
        for rows in _xls_sheets(data):
            header_idx, cols = _locate_header(rows)
            if cols:
                return _parse_rows(rows, header_idx, cols)
        raise ValueError(
            "Could not find the statement header in the Excel file. It needs Date, "
            "Narration/Description and Withdrawal/Debit and Deposit/Credit columns."
        )
    if data.startswith(ZIP_MAGIC):
        raise ValueError("Excel .xlsx files are not supported; save the statement as .xls or CSV.")
    return parse_statement(_decode_text(data))


def parse_statement(text):
    text = (text or "").lstrip("\ufeff")
    header_idx, cols, rows = None, None, []
    for delim in [",", "\t", ";", "|"]:
        rows = list(csv.reader(io.StringIO(text), delimiter=delim))
        header_idx, cols = _locate_header(rows)
        if cols:
            break
    if not cols:
        raise ValueError(
            "Could not find the statement header. The CSV needs Date, Narration/Description "
            "and Debit/Withdrawal and Credit/Deposit columns."
        )
    return _parse_rows(rows, header_idx, cols)


def _parse_rows(rows, header_idx, cols):
    entries, seen = [], Counter()
    for row in rows[header_idx + 1:]:
        txn_date = parse_date(_cell(row, cols["date"]))
        if not txn_date:
            continue  # footers, blank lines, opening-balance rows
        desc = re.sub(r"\s+", " ", _cell(row, cols["desc"]))
        debit = parse_amount(_cell(row, cols["debit"]))
        credit = parse_amount(_cell(row, cols["credit"]))
        if debit == 0 and credit == 0:
            continue
        bal_txt = _cell(row, cols["balance"])
        balance = parse_amount(bal_txt) if bal_txt else None

        ref = _cell(row, cols["ref"])
        if not ref or set(ref) <= {"0"}:
            m = re.search(r"(?<!\d)\d{12}(?!\d)", desc)  # UPI reference numbers are 12 digits
            ref = m.group() if m else None

        key = (txn_date, desc.lower(), f"{debit:.2f}", f"{credit:.2f}", str(balance), ref or "")
        occurrence = seen[key]  # identical rows inside one file stay distinct
        seen[key] += 1
        digest = hashlib.sha256("|".join(key + (str(occurrence),)).encode()).hexdigest()
        entries.append({
            "entry_hash": digest, "txn_date": txn_date, "description": desc,
            "txn_ref": ref, "debit": debit, "credit": credit, "balance": balance,
        })
    if not entries:
        raise ValueError("No transactions were found in the file.")
    return entries
