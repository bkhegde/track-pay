# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Product spec

Track-Pay tracks monthly fees from customers and reconciles them against bank statements. Required features:

1. Customer CRUD: name, phone, monthly fee, status, one or more Indian UPI ids. Checkbox selection to activate/deactivate individually or in bulk.
2. Generate one invoice per active customer for a given month (idempotent). Update invoice amounts individually or in bulk for a month.
3. Mark invoices paid by manually entering paid amount and (optional) date.
4. Upload a bank statement; store entries in a table idempotently.
5. Auto reconcile bank entries to invoices using the UPI id; on match, set paid amount, payment date and transaction id from the bank entry.
6. Side-by-side view of invoices and bank entries for manual matching.
7. Filter to view/edit only unpaid invoices.

Use a good-quality HTML grid for viewing and editing data (AG Grid Community is used).

## Commands

```bash
pip install -r requirements.txt                     # only dependency: pywebview
cd ui && npm install && npm run build && cd ..      # build runs svelte-check, then vite build -> ui/dist
python main.py                                      # launch desktop app (loads ui/dist/index.html)
```

Hot reload: run `npm run dev` in `ui/` (Vite on fixed port 5173), then start Python with `TRACKPAY_DEV=1` (PowerShell: `$env:TRACKPAY_DEV=1; python main.py`) — the window loads the dev server and enables webview devtools.

Type-check the UI without building: `cd ui && npm run check`. There is no test suite or Python linter configured.

Database lives at `~/.trackpay/trackpay.db`; set `TRACKPAY_DB=<path>` to use a different file (handy for experiments). The schema is created with `CREATE TABLE IF NOT EXISTS` on startup — there are no migrations, so schema changes to existing tables need manual handling.

## Architecture

Desktop app: a `pywebview` window (`main.py`) hosts a Svelte 5 + TypeScript UI, and the Python `Api` class is injected as `js_api`. There is no HTTP backend — the UI calls Python directly over the pywebview bridge.

**Python side**
- `api.py` — all business logic. Every public method of `Api` is callable from JS, so helpers/state must be underscore-prefixed (or module-level) to stay hidden. Public methods are wrapped in `@safe`, which returns `{"data": ...}` or `{"error": msg}`; raise `ValueError` for user-facing validation messages.
- `db.py` — schema + `Database.tx()` context manager. A new SQLite connection is opened per call because pywebview invokes the API from worker threads; always do DB work inside `with self._db.tx() as c:`.
- `bank_parser.py` — CSV statement parser. Auto-detects delimiter and the header row (lines above it are ignored), maps Date / Narration / Debit / Credit / optional Ref & Balance columns, and falls back to extracting a 12-digit UPI ref from the narration.

**UI side (`ui/src`)**
- `lib/api.ts` — typed `call(method, ...args)` wrapper; unwraps `{data}` and throws on `{error}`. The `Api` interface there mirrors the Python method signatures, so **adding/changing a Python API method requires updating this interface** (and `lib/types.ts` for row shapes).
- `components/Grid.svelte` — the single AG Grid wrapper used by every tab (checkbox multi-select, single-click editing, emits `selection` and `cellchange` events). Rows must have a numeric `id` (used as row id).
- `lib/ui.ts` — toasts (`notify`), in-app confirm dialog (`ask` — use instead of `window.confirm`, which is unreliable in webviews), INR formatting.
- `App.svelte` switches between tabs: Customers, Invoices, Bank statement, Reconcile (side-by-side manual matching).
- Components use legacy Svelte syntax (`export let`, `$:`, `createEventDispatcher`), not runes — follow that style.

## Data invariants

- Invoices: `UNIQUE(customer_id, month)`, month is `YYYY-MM`; generation uses insert-or-ignore so re-running only adds missing invoices.
- Bank entries: idempotency via `entry_hash` (sha256 of date/description/amounts/balance/ref plus an occurrence counter so identical rows within one file stay distinct). Re-uploading a file is safe.
- UPI ids are stored lower-case and globally unique (`customer_upi.upi_id`).
- Invoice ↔ bank entry link is stored on both sides (`invoices.bank_entry_id` and `bank_entries.matched_invoice_id`); `_link` sets both and `_mark_unpaid` clears both — keep them in sync.
- Auto reconcile: an unmatched credit matches when exactly one customer's UPI id appears in the narration (word-boundary regex) and that customer has an UNPAID invoice of the exact amount; oldest month wins. Multiple owners → counted as ambiguous, skipped.
- Customers with invoices cannot be deleted: `delete_customers` skips them and returns their names. They have to be deactivated instead.
