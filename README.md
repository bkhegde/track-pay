# Track-Pay

Desktop app: Svelte + TypeScript UI (`ui/`) (AG Grid) in a Python `pywebview` window, business logic in Python, data in SQLite
(`~/.trackpay/trackpay.db`, override with `TRACKPAY_DB`).

## Run
```bash
pip install -r requirements.txt
cd ui && npm install && npm run build && cd ..
python main.py
```

## Develop with hot reload
```bash
cd ui && npm run dev          # terminal 1
TRACKPAY_DEV=1 python main.py       # terminal 2 (PowerShell: $env:TRACKPAY_DEV=1; python main.py)
```

## Bank statement CSV
Needs a header row with Date, Narration/Description, Withdrawal/Debit and Deposit/Credit columns
(optional: Ref/UTR, Balance). Lines above the header are ignored. Re-uploading a file is safe.

## Rules
* Invoices: one per customer per month (`UNIQUE(customer_id, month)`); generating again only adds missing ones.
* Auto reconcile: a credit matches when a customer's UPI id appears in its narration and an unpaid invoice has the same amount (oldest month first).
* Customers with invoices cannot be deleted — deactivate them.
