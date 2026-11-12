# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Product spec

Track-Pay tracks monthly fees from customers and reconciles them against bank statements. Required features:

1. Customer CRUD: name, phone, monthly fee, status, one or more Indian UPI ids. Checkbox selection to activate/deactivate individually or in bulk.
2. Generate one invoice per active customer for a given month (idempotent). Update invoice amounts individually or in bulk for a month.
3. Record cash payments (customer, amount, optional date/note). Payments may be partial or cover several invoices.
4. Upload a bank statement; store entries in a table idempotently.
5. Auto reconcile bank entries to customers using name + UPI id; a match posts the bank credit as a payment.
6. Side-by-side view of invoices and bank entries for manual matching.
7. Filter to view/edit only unpaid (outstanding) invoices. A Ledger tab shows the journal.

Use a good-quality HTML grid for viewing and editing data (AG Grid Community is used).

## Commands

```bash
pip install -r requirements.txt                     # pywebview, xlrd (for .xls statements)
cd ui && npm install && npm run build && cd ..      # build runs svelte-check, then vite build -> ui/dist
python main.py                                      # launch desktop app (loads ui/dist/index.html)
```

Hot reload: run `npm run dev` in `ui/` (Vite on fixed port 5173), then start Python with `TRACKPAY_DEV=1` (PowerShell: `$env:TRACKPAY_DEV=1; python main.py`) — the window loads the dev server and enables webview devtools.

Type-check the UI without building: `cd ui && npm run check`. There is no test suite or Python linter configured.

Database lives at `data/trackpay.db` (a legacy `~/.trackpay/trackpay.db` is copied there once on first run); set `TRACKPAY_DB=<path>` to use a different file (handy for experiments). `Database` takes a weekly snapshot (sqlite backup API) into `backups/` on startup (plus a Backup button in the top bar), keeping the newest 12; set `TRACKPAY_BACKUP_DIR=<path>` to put them elsewhere (e.g. another drive). The schema is created with `CREATE TABLE IF NOT EXISTS` on startup — there are no migrations, so schema changes to existing tables need manual handling.

## Architecture

Desktop app: a `pywebview` window (`main.py`) hosts a Svelte 5 + TypeScript UI, and the Python `Api` class is injected as `js_api`. There is no HTTP backend — the UI calls Python directly over the pywebview bridge.

**Python side**
- `api.py` — all business logic. Every public method of `Api` is callable from JS, so helpers/state must be underscore-prefixed (or module-level) to stay hidden. Public methods are wrapped in `@safe`, which returns `{"data": ...}` or `{"error": msg}`; raise `ValueError` for user-facing validation messages.
- `db.py` — schema + `Database.tx()` context manager. A new SQLite connection is opened per call because pywebview invokes the API from worker threads; always do DB work inside `with self._db.tx() as c:`.
- `bank_parser.py` — statement parser for CSV/text and legacy `.xls` (via `xlrd`, imported lazily). `parse_statement_file(bytes)` sniffs the format from magic bytes (`.xlsx` is rejected with a message; text is decoded utf-8-sig → cp1252 → latin-1), and for `.xls` uses the first sheet with a recognisable header. Both paths share header detection (lines above it are ignored) and `_parse_rows`, mapping Date / Narration / Debit / Credit / optional Ref & Balance columns and falling back to extracting a 12-digit UPI ref from the narration. The UI sends the uploaded file base64-encoded to `import_bank_statement(filename, content_b64)` so binary files survive the bridge.

**UI side (`ui/src`)**
- `lib/api.ts` — typed `call(method, ...args)` wrapper; unwraps `{data}` and throws on `{error}`. The `Api` interface there mirrors the Python method signatures, so **adding/changing a Python API method requires updating this interface** (and `lib/types.ts` for row shapes).
- `components/Grid.svelte` — the single AG Grid wrapper used by every tab (checkbox multi-select, single-click editing, emits `selection` and `cellchange` events). Rows must have a numeric `id` (used as row id).
- `lib/ui.ts` — toasts (`notify`), in-app confirm dialog (`ask` — use instead of `window.confirm`, which is unreliable in webviews), INR formatting.
- `App.svelte` switches between tabs: Customers, Invoices, Bank statement, Reconcile (side-by-side manual matching), Ledger (journal postings; remove payments).
- Components use legacy Svelte syntax (`export let`, `$:`, `createEventDispatcher`), not runes — follow that style.

## Data invariants

- Invoices: `UNIQUE(customer_id, month)`, month is `YYYY-MM`; generation uses insert-or-ignore so re-running only adds missing invoices.
- Bank entries: idempotency via `entry_hash` (sha256 of date/description/amounts/balance/ref plus an occurrence counter so identical rows within one file stay distinct). Re-uploading a file is safe.
- UPI ids are stored lower-case and globally unique (`customer_upi.upi_id`).
- **Journal (`transactions` table) is the source of truth for money.** Generating an invoice posts a NEGATIVE amount (kind `INVOICE`, one per invoice, kept in step with `invoices.amount`); every payment is a POSITIVE amount (kind `BANK` from a bank-entry match, auto or manual; kind `CASH` from the cash-payment dialog). A CHECK constraint enforces the signs. Never store paid state on invoices.
- Invoice paid/partial/unpaid status, paid amount, last paid date and transaction ids are **derived** by `Api._allocate`: payments aimed at a specific invoice (`transactions.invoice_id`, set by manual match / cash-for-selected-invoice) settle it first, the rest settles the customer's invoices oldest month first; leftover is an advance. `list_invoices` status `UNPAID` means anything not fully paid (partials included). Only fully paid invoices are locked against amount edits.
- Each bank entry can back at most one payment (`transactions.bank_entry_id UNIQUE`); "matched" bank entries are those with such a transaction. Unmatching = `delete_payments`. Legacy columns (`invoices.status/paid_*/txn_id/bank_entry_id`, `bank_entries.matched_invoice_id`) are unused; `db.py` `MIGRATE_LEGACY` converts old data into journal postings on startup.
- Auto reconcile: an unmatched credit is posted when exactly one customer is found in the narration by BOTH name (all name words, in order, whole words) AND UPI id (statements show only the last 4 chars before `@` plus the full handle, e.g. `1234@okaxis`), and that customer has an outstanding invoice (in the chosen month, if given). The amount need NOT equal an invoice (partial / multi-invoice / excess-as-advance all fine). If one matching name contains another (Ravi / Ravi Kumar) only the longest counts. Multiple owners → counted as ambiguous, skipped. A manual match saves the payer's masked UPI id (from the narration) on the customer (`_learn_upi`), so later months auto-match; it never takes an id already owned by someone. Customers with no UPI id are never auto-matched.
- Customers with invoices or journal postings cannot be deleted: `delete_customers` skips them and returns their names. They have to be deactivated instead.
