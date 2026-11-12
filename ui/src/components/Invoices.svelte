<script lang="ts">
  import { onMount } from 'svelte';
  import type { CellValueChangedEvent, ColDef, ICellRendererParams } from 'ag-grid-community';
  import Grid from './Grid.svelte';
  import { call } from '../lib/api';
  import { notify, ask, inr, currentMonth } from '../lib/ui';
  import type { Customer, Invoice } from '../lib/types';

  let month = currentMonth();
  let unpaidOnly = false;
  let rows: Invoice[] = [];
  let selected: Invoice[] = [];
  let search = '';
  let bulkAmount: number | null = null; // number inputs bind to null when empty
  let showPay = false;
  let customers: Customer[] = [];
  let pay: { customerId: number | null; invoiceId: number | null; amount: number | null; date: string; note: string } =
    { customerId: null, invoiceId: null, amount: null, date: '', note: '' };

  $: ids = selected.map((r) => r.id);
  $: totals = {
    due: rows.reduce((s, r) => s + r.balance, 0),
    paid: rows.reduce((s, r) => s + (r.paid_amount ?? 0), 0)
  };

  const columns: ColDef<Invoice>[] = [
    { field: 'customer_name', headerName: 'Customer', flex: 1.3, minWidth: 160 },
    { field: 'phone', headerName: 'Phone', width: 130 },
    { field: 'month', headerName: 'Month', width: 105 },
    {
      field: 'amount', headerName: 'Invoice amount', width: 150, type: 'rightAligned',
      editable: (p) => p.data?.status !== 'PAID',
      cellEditor: 'agNumberCellEditor', cellEditorParams: { min: 0 },
      valueFormatter: (p) => inr(p.value), valueParser: (p) => Number(p.newValue)
    },
    {
      field: 'status', headerName: 'Status', width: 110,
      cellRenderer: (p: ICellRendererParams<Invoice, Invoice['status']>) =>
        `<span class="badge ${(p.value ?? 'UNPAID').toLowerCase()}">${p.value === 'PAID' ? 'Paid' : p.value === 'PARTIAL' ? 'Partial' : 'Unpaid'}</span>`
    },
    { field: 'paid_amount', headerName: 'Paid amount', width: 130, type: 'rightAligned', valueFormatter: (p) => inr(p.value) },
    { field: 'balance', headerName: 'Balance', width: 120, type: 'rightAligned', valueFormatter: (p) => inr(p.value) },
    { field: 'paid_date', headerName: 'Last paid on', width: 125 },
    { field: 'txn_id', headerName: 'Transaction id', width: 170 },
    { field: 'paid_via', headerName: 'Paid via', width: 110 }
  ];

  async function load() {
    try {
      rows = await call('list_invoices', month, unpaidOnly ? 'UNPAID' : 'ALL');
    } catch (e) { notify((e as Error).message, 'error'); }
  }
  onMount(load);
  $: month, unpaidOnly, load();

  async function generate() {
    try {
      const r = await call('generate_invoices', month);
      notify(`${r.created} invoice(s) created, ${r.existing} already existed for ${month}`, 'success');
      await load();
    } catch (e) { notify((e as Error).message, 'error'); }
  }

  async function onCellChange(e: CustomEvent<CellValueChangedEvent<Invoice>>) {
    const { colDef, data } = e.detail;
    try {
      if (!data) return;
      if (colDef.field === 'amount') await call('update_invoice_amounts', [data.id], data.amount);
    } catch (err) { notify((err as Error).message, 'error'); }
    await load();
  }

  async function applyToSelected() {
    try {
      const r = await call('update_invoice_amounts', ids, bulkAmount);
      notify(`${r.updated} invoice(s) updated (fully paid invoices are left unchanged)`, 'success');
      await load();
    } catch (e) { notify((e as Error).message, 'error'); }
  }

  async function applyToMonth() {
    if (!(await ask(`Set every invoice of ${month} that is not fully paid to ${inr(bulkAmount)}?`, 'Update all'))) return;
    try {
      const r = await call('update_month_amounts', month, bulkAmount);
      notify(`${r.updated} invoice(s) updated`, 'success');
      await load();
    } catch (e) { notify((e as Error).message, 'error'); }
  }

  // Cash payment: posted to the customer's account. It settles the selected invoice first (when exactly
  // one is selected), then the oldest outstanding invoices.
  async function openPay() {
    try { customers = await call('list_customers'); } catch (e) { notify((e as Error).message, 'error'); return; }
    const owing = selected.filter((r) => r.balance > 0);
    const first = owing[0] ?? selected[0];
    const mine = first ? owing.filter((r) => r.customer_id === first.customer_id) : [];
    pay = {
      customerId: first?.customer_id ?? null,
      invoiceId: selected.length === 1 && first.balance > 0 ? first.id : null,
      amount: mine.length ? mine.reduce((s, r) => s + r.balance, 0) : null,
      date: '', note: ''
    };
    showPay = true;
  }

  async function recordCash() {
    try {
      if (pay.customerId == null) throw new Error('Choose a customer');
      await call('add_cash_payment', pay.customerId, pay.amount as number, pay.date || null, pay.note || null, pay.invoiceId);
      showPay = false;
      notify(`Cash payment of ${inr(pay.amount)} recorded`, 'success');
      await load();
    } catch (e) { notify((e as Error).message, 'error'); }
  }

  const DEFAULT_TEMPLATE =
    'Hi {name}, this is a gentle reminder that your fee of {amount} for {month} is pending. Kindly pay at your earliest convenience. Thank you!';
  let showRemind = false;
  let template = DEFAULT_TEMPLATE;
  let opened = new Set<number>();

  $: unpaidSelected = selected.filter((r) => r.status !== 'PAID');

  function openRemind() {
    try { template = localStorage.getItem('reminderTemplate') || DEFAULT_TEMPLATE; } catch { /* storage unavailable */ }
    opened = new Set();
    showRemind = true;
  }

  function monthLabel(m: string) {
    return new Date(`${m}-01T00:00:00`).toLocaleString('en-IN', { month: 'long', year: 'numeric' });
  }

  // {amount} is what is still outstanding on the invoice
  function render(r: Invoice, t: string) {
    return t.replaceAll('{name}', r.customer_name).replaceAll('{amount}', inr(r.balance))
      .replaceAll('{month}', monthLabel(r.month));
  }

  async function remind(r: Invoice) {
    try {
      await call('open_whatsapp', r.phone ?? '', render(r, template));
      opened = new Set(opened).add(r.id);
    } catch (e) { notify((e as Error).message, 'error'); }
  }

  function saveTemplate() {
    try { localStorage.setItem('reminderTemplate', template); } catch { /* storage unavailable */ }
  }
</script>

<div class="view">
  <div class="toolbar">
    <label>Month <input type="month" bind:value={month} /></label>
    <button class="btn primary" on:click={generate}>Generate invoices</button>
    <label class="check"><input type="checkbox" bind:checked={unpaidOnly} /> Show unpaid only</label>
    <span class="spacer"></span>
    <span class="count">Due {inr(totals.due)} · Collected {inr(totals.paid)}</span>
    <input type="text" placeholder="Search" bind:value={search} />
  </div>

  <div class="toolbar">
    <input type="number" min="0" step="0.01" placeholder="New amount" style="width:130px" bind:value={bulkAmount} />
    <button class="btn" disabled={!ids.length || bulkAmount == null} on:click={applyToSelected}>Set amount for selected</button>
    <button class="btn" disabled={bulkAmount == null} on:click={applyToMonth}>Set amount for all unpaid in month</button>
    <span class="sep"></span>
    <button class="btn" on:click={openPay}>Record cash payment</button>
    <button class="btn" disabled={!unpaidSelected.length} on:click={openRemind}>WhatsApp reminder</button>
    <span class="spacer"></span>
    <span class="count">{ids.length} of {rows.length} selected</span>
  </div>

  <div class="grid-wrap">
    <Grid columnDefs={columns} rowData={rows} quickFilter={search}
          on:selection={(e) => (selected = e.detail)} on:cellchange={onCellChange} />
  </div>
</div>

{#if showRemind}
  <div class="backdrop">
    <div class="modal" role="dialog" aria-modal="true" style="min-width:560px">
      <h3>WhatsApp reminder · {unpaidSelected.length} unpaid invoice(s)</h3>
      <label class="row">
        <span>Message</span>
        <textarea rows="4" bind:value={template} on:change={saveTemplate}></textarea>
        <span class="hint">Placeholders: {'{name}'}, {'{amount}'} (outstanding), {'{month}'}. Each button opens WhatsApp with the message ready; press Send there.</span>
      </label>
      <div style="max-height:280px;overflow:auto">
        {#each unpaidSelected as r (r.id)}
          <div class="toolbar">
            <span>{r.customer_name} · {inr(r.balance)} · {r.phone || 'no phone'}</span>
            <span class="spacer"></span>
            <button class="btn" disabled={!r.phone} on:click={() => remind(r)}>
              {opened.has(r.id) ? 'Opened ✓ – open again' : 'Open WhatsApp'}
            </button>
          </div>
        {/each}
      </div>
      <div class="actions">
        <button class="btn" on:click={() => (showRemind = false)}>Close</button>
      </div>
    </div>
  </div>
{/if}

{#if showPay}
  <div class="backdrop">
    <div class="modal" role="dialog" aria-modal="true">
      <h3>Record cash payment</h3>
      <label class="row">
        <span>Customer</span>
        <select bind:value={pay.customerId} on:change={() => (pay.invoiceId = null)}>
          <option value={null}>Choose…</option>
          {#each customers as c (c.id)}<option value={c.id}>{c.name}</option>{/each}
        </select>
      </label>
      <label class="row">
        <span>Amount (₹)</span>
        <input type="number" min="0" step="0.01" bind:value={pay.amount} />
        <span class="hint">
          {pay.invoiceId
            ? 'Settles the selected invoice first, then the oldest outstanding ones.'
            : 'Partial amounts are fine. It settles the oldest outstanding invoices first; any excess stays as an advance.'}
        </span>
      </label>
      <label class="row">
        <span>Paid on (optional, defaults to today)</span>
        <input type="date" bind:value={pay.date} />
      </label>
      <label class="row">
        <span>Note (optional)</span>
        <input type="text" bind:value={pay.note} />
      </label>
      <div class="actions">
        <button class="btn" on:click={() => (showPay = false)}>Cancel</button>
        <button class="btn primary" on:click={recordCash}>Record payment</button>
      </div>
    </div>
  </div>
{/if}
