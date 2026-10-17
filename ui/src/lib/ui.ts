import { writable } from 'svelte/store';

// ---- toasts
export type ToastType = 'info' | 'success' | 'error';
export interface Toast {
  id: number;
  message: string;
  type: ToastType;
}

export const toasts = writable<Toast[]>([]);
let seq = 0;
export function notify(message: string, type: ToastType = 'info', ms = 4500): void {
  const id = ++seq;
  toasts.update((t) => [...t, { id, message, type }]);
  setTimeout(() => toasts.update((t) => t.filter((x) => x.id !== id)), ms);
}

// ---- in-app confirm dialog (window.confirm is unreliable in some webviews)
export interface ConfirmState {
  message: string;
  okLabel: string;
  resolve: (ok: boolean) => void;
}

export const confirmState = writable<ConfirmState | null>(null);
export function ask(message: string, okLabel = 'Confirm'): Promise<boolean> {
  return new Promise((resolve) => {
    confirmState.set({ message, okLabel, resolve });
  });
}

// ---- helpers
const inrFmt = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' });
export const inr = (v: number | string | null | undefined): string =>
  v === null || v === undefined || v === '' ? '' : inrFmt.format(Number(v));

export function currentMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}
