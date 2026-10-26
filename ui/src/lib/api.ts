// Thin wrapper around the Python API exposed by pywebview (window.pywebview.api).
import type {
  BankEntry, BankView, Customer, CustomerInput, ImportResult, Invoice, ReconcileResult, Updated
} from './types';

// Method signatures of the Python Api class; each returns {data} or {error} over the bridge.
export interface Api {
  list_customers(): Customer[];
  save_customer(data: CustomerInput): { id: number };
  set_customers_active(ids: number[], active: boolean): Updated;
  delete_customers(ids: number[]): { deleted: number; skipped: string[] };
  generate_invoices(month: string): { created: number; existing: number };
  list_invoices(month: string, status: 'ALL' | 'PAID' | 'UNPAID'): Invoice[];
  update_invoice_amounts(ids: number[], amount: number | null): Updated;
  update_month_amounts(month: string, amount: number | null): Updated;
  mark_paid(ids: number[], paidAmount: number | null, paidDate: string | null): Updated;
  update_payment(invoiceId: number, paidAmount: number | null, paidDate: string | null): true;
  mark_unpaid(ids: number[]): Updated;
  backup_database(): { path: string };
  import_bank_statement(filename: string, contentBase64: string): ImportResult;
  list_bank_entries(view: BankView): BankEntry[];
  auto_reconcile(month: string | null): ReconcileResult;
  manual_match(invoiceId: number, bankEntryId: number): unknown;
}

type Method = keyof Api;
type Result<T> = { data: T; error?: undefined } | { error: string; data?: undefined };

declare global {
  interface Window {
    pywebview?: { api: { [M in Method]: (...args: Parameters<Api[M]>) => Promise<Result<ReturnType<Api[M]>>> } };
  }
}

function ready(): Promise<void> {
  return new Promise((resolve) => {
    if (window.pywebview && window.pywebview.api) resolve();
    else window.addEventListener('pywebviewready', () => resolve(), { once: true });
  });
}

export async function call<M extends Method>(method: M, ...args: Parameters<Api[M]>): Promise<ReturnType<Api[M]>> {
  await ready();
  const fn = window.pywebview!.api[method] as (...a: Parameters<Api[M]>) => Promise<Result<ReturnType<Api[M]>>>;
  const res = await fn(...args);
  if (res && res.error) throw new Error(res.error);
  return res.data as ReturnType<Api[M]>;
}
