<script>
  import { onMount } from 'svelte';
  import Grid from './Grid.svelte';
  import { call } from '../lib/api.js';
  import { notify, ask, inr, currentMonth } from '../lib/ui.js';

  let month = currentMonth();
  let unpaidOnly = false;
  let rows = [];
  let selected = [];
  let search = '';
  let bulkAmount = '';
  let showPay = false;
  let pay = { amount: '', date: '' };

  $: ids = selected.map((r) => r.id);
  $: totals = {
    due: rows.filter((r) => r.status === 'UNPAID').reduce((s, r) => s + r.amount, 0),
    paid: rows.filter((r) => r.status === 'PAID').reduce((s, r) => s + (r.paid_amount ?? 0), 0)
  };

  const columns = [
    { field: 'customer_name', headerName: 'Customer', flex: 1.3, minWidth: 160 },
    { field: 'phone', headerName: 'Phone', width: 130 },
    { field: 'month', headerName: 'Month', width: 105 },
    {
      field: 'amount', headerName: 'Invoice amount', width: 150, type: 'rightAligned',
      editable: (p) => p.data.status === 'UNPAID',
      cellEditor: 'agNumberCellEditor', cellEditorParams: { min: 0 },
      valueFormatter: (p) => inr(p.value), valueParser: (p) => Number(p.newValue)
    },
    {
      field: 'status', headerName: 'Status', width: 110,
      cellRenderer: (p) => `<span class="badge ${p.value === 'PAID' ? 'paid' : 'unpaid'}">${p.value === 'PAID' ? 'Paid' : 'Unpaid'}</span>`
    },
    {
      field: 'paid_amount', headerName: 'Paid amount', width: 140, type: 'rightAligned', editable: true,
      cellEditor: 'agNumberCellEditor', cellEditorParams: { min: 0 },
      valueFormatter: (p) => inr(p.value),
      valueParser: (p) => (p.newValue === '' || p.newValue == null ? null : Number(p.newValue))
    },
    {
      field: 'paid_date', headerName: 'Paid on', width: 140, editable: true,
      cellDataType: 'dateString'
    },
    { field: 'txn_id', headerName: 'Transaction id', width: 170 },
    { field: 'paid_via', headerName: 'Paid via', width: 100 }
  ];

  async function load() {
    try {
      rows = await call('list_invoices', month, unpaidOnly ? 'UNPAID' : 'ALL');
    } catch (e) { notify(e.message, 'error'); }
  }
  onMount(load);
  $: month, unpaidOnly, load();

  async function generate() {
    try {
      const r = await call('generate_invoices', month);
      notify(`${r.created} invoice(s) created, ${r.existing} already existed for ${month}`, 'success');
      await load();
    } catch (e) { notify(e.message, 'error'); }
  }

  async function onCellChange(e) {
    const { colDef, data } = e.detail;
    try {
      if (colDef.field === 'amount') await call('update_invoice_amounts', [data.id], data.amount);
      else await call('update_payment', data.id, data.paid_amount ?? null, data.paid_date || null);
    } catch (err) { notify(err.message, 'error'); }
    await load();
  }

  async function applyToSelected() {
    try {
      const r = await call('update_invoice_amounts', ids, bulkAmount);
      notify(`${r.updated} unpaid invoice(s) updated (paid invoices are left unchanged)`, 'success');
      await load();
    } catch (e) { notify(e.message, 'error'); }
  }

  async function applyToMonth() {
    if (!(await ask(`Set every unpaid invoice of ${month} to ${inr(bulkAmount)}?`, 'Update all'))) return;
    try {
      const r = await call('update_month_amounts', month, bulkAmount);
      notify(`${r.updated} invoice(s) updated`, 'success');
      await load();
    } catch (e) { notify(e.message, 'error'); }
  }

  function openPay() { pay = { amount: '', date: '' }; showPay = true; }

  async function markPaid() {
    try {
      const r = await call('mark_paid', ids, pay.amount === '' ? null : pay.amount, pay.date || null);
      showPay = false;
      notify(`${r.updated} invoice(s) marked as paid`, 'success');
      await load();
    } catch (e) { notify(e.message, 'error'); }
  }

  async function markUnpaid() {
    if (!(await ask(`Mark ${ids.length} invoice(s) as unpaid? Linked bank entries are released.`, 'Mark unpaid'))) return;
    try {
      await call('mark_unpaid', ids);
      await load();
    } catch (e) { notify(e.message, 'error'); }
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
    <button class="btn" disabled={!ids.length || bulkAmount === ''} on:click={applyToSelected}>Set amount for selected</button>
    <button class="btn" disabled={bulkAmount === ''} on:click={applyToMonth}>Set amount for all unpaid in month</button>
    <span class="sep"></span>
    <button class="btn" disabled={!ids.length} on:click={openPay}>Mark selected as paid</button>
    <button class="btn" disabled={!ids.length} on:click={markUnpaid}>Mark selected as unpaid</button>
    <span class="spacer"></span>
    <span class="count">{ids.length} of {rows.length} selected</span>
  </div>

  <div class="grid-wrap">
    <Grid columnDefs={columns} rowData={rows} quickFilter={search}
          on:selection={(e) => (selected = e.detail)} on:cellchange={onCellChange} />
  </div>
</div>

{#if showPay}
  <div class="backdrop">
    <div class="modal" role="dialog" aria-modal="true">
      <h3>Mark {ids.length} invoice(s) as paid</h3>
      <label class="row">
        <span>Paid amount (₹)</span>
        <input type="number" min="0" step="0.01" bind:value={pay.amount} />
        <span class="hint">Leave blank to use each invoice's own amount.</span>
      </label>
      <label class="row">
        <span>Paid on (optional)</span>
        <input type="date" bind:value={pay.date} />
      </label>
      <div class="actions">
        <button class="btn" on:click={() => (showPay = false)}>Cancel</button>
        <button class="btn primary" on:click={markPaid}>Mark as paid</button>
      </div>
    </div>
  </div>
{/if}
