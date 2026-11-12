// Shapes of the rows and results returned by the Python API (api.py).

export interface Customer {
  id: number;
  name: string;
  phone: string | null;
  monthly_fee: number;
  active: boolean;
  upi_ids: string; // comma separated
}

export type CustomerInput = Omit<Customer, 'id'> & { id: number | null };

export type InvoiceStatus = 'PAID' | 'PARTIAL' | 'UNPAID';

export interface Invoice {
  id: number;
  customer_id: number;
  customer_name: string;
  phone: string | null;
  month: string; // YYYY-MM
  amount: number;
  status: InvoiceStatus;
  paid_amount: number | null; // allocated from payments in the journal
  balance: number;            // amount still outstanding
  paid_date: string | null; // YYYY-MM-DD
  txn_id: string | null;
  paid_via: string; // 'Bank', 'Cash', 'Bank + Cash' or ''
  upi_ids: string;
}

export type BankView = 'ALL' | 'UNMATCHED_CREDITS' | 'MATCHED';

export interface BankEntry {
  id: number;
  txn_date: string; // YYYY-MM-DD
  description: string | null;
  txn_ref: string | null;
  debit: number;
  credit: number;
  balance: number | null;
  matched_customer_id: number | null;
  matched_to: string;
}

export type TxnKind = 'INVOICE' | 'BANK' | 'CASH';

// One journal posting: invoices are negative (debit), payments positive (credit).
export interface Transaction {
  id: number;
  customer_id: number;
  customer_name: string;
  txn_date: string; // YYYY-MM-DD
  kind: TxnKind;
  amount: number;
  txn_ref: string | null;
  invoice_month: string | null;
  description: string;
  balance: number; // customer's running balance after this posting (negative = owes)
}

export interface ImportResult {
  total: number;
  inserted: number;
  duplicates: number;
}

export interface ReconcileResult {
  matched: number;
  no_match: number;
  ambiguous: number;
  no_invoice: number;
  details: string[];
}

export interface Updated {
  updated: number;
}
