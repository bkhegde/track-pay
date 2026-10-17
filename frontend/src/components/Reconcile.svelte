<script>
  import { onMount } from 'svelte';
  import Grid from './Grid.svelte';
  import { call } from '../lib/api.js';
  import { notify, inr, currentMonth } from '../lib/ui.js';

  let autoMonth = '';           // empty = all months
  let result = null;
  let invoices = [];
  let entries = [];
  let invSel = null;
  let entrySel = null;
  let invSearch = '';
  let bankSearch = '';
  let suggest = true;
  let running = false;

  const invCols = [
    { field: 'customer_name', headerName: 'Customer', flex: 1.2, minWidth: 130 },
    { field: 'month', headerName: 'Month', width: 100 },
    { field: 'amount', headerName: 'Amount', width: 115, type: 'rightAligned', valueFormatter: (p) => inr(p.value) },
    { field: 'upi_ids', headerName: 'UPI ids', flex: 1.2, minWidth: 140 }
  ];
  const bankCols = [
    { field: 'txn_date', headerName: 'Date', width: 105, sort: 'desc' },
    { field: 'description', headerName: 'Narration', flex: 2, minWidth: 200, tooltipField: 'description' },
    { field: 'credit', headerName: 'Credit', width: 115, type: 'rightAligned', valueFormatter: (p) => inr(p.value) }
  ];

  async function load() {
    try {
      [invoices, entries] = await Promise.all([
        call('list_invoices', 'ALL', 'UNPAID'),
        call('list_bank_entries', 'UNMATCHED_CREDITS')
      ]);
      invSel = null;
      entrySel = null;
    } catch (e) { notify(e.message, 'error'); }
  }
  onMount(load);

  async function autoReconcile() {
    running = true;
    try {
      result = await call('auto_reconcile', autoMonth || null);
      notify(`${result.matched} payment(s) matched automatically`, result.matched ? 'success' : 'info');
      await load();
    } catch (e) { notify(e.message, 'error'); } finally { running = false; }
  }

  function onInvoiceSelect(e) {
    invSel = e.detail[0] || null;
    if (suggest && invSel) {
      const first = (invSel.upi_ids || '').split(',')[0].trim();
      bankSearch = first;   // narrow the bank list to this customer's UPI id
    }
  }

  $: amountMismatch = invSel && entrySel && Math.abs(invSel.amount - entrySel.credit) > 0.005;

  async function match() {
    try {
      await call('manual_match', invSel.id, entrySel.id);
      notify('Payment matched and invoice marked as paid', 'success');
      await load();
    } catch (e) { notify(e.message, 'error'); }
  }
</script>

<div class="view">
  <div class="toolbar">
    <label>Invoice month
      <select bind:value={autoMonth}>
        <option value="">All months</option>
        {#each [...new Set(invoices.map((i) => i.month))] as m}<option value={m}>{m}</option>{/each}
      </select>
    </label>
    <button class="btn primary" disabled={running} on:click={autoReconcile}>
      {running ? 'Reconciling…' : 'Auto reconcile by UPI id'}
    </button>
    <span class="muted">Matches credits whose narration contains a customer's UPI id and whose amount equals an unpaid invoice.</span>
  </div>

  {#if result}
    <div class="panel">
      <div class="stats">
        <span><b>{result.matched}</b> matched</span>
        <span><b>{result.no_invoice}</b> UPI found, no invoice of that amount</span>
        <span><b>{result.ambiguous}</b> ambiguous</span>
        <span><b>{result.no_upi_match}</b> no customer UPI id</span>
      </div>
      {#if result.details.length}<ul>{#each result.details as d}<li>{d}</li>{/each}</ul>{/if}
    </div>
  {/if}

  <div class="split">
    <div class="pane">
      <h3>Unpaid invoices ({invoices.length})</h3>
      <div class="toolbar">
        <input type="text" placeholder="Search invoices" bind:value={invSearch} />
        <label class="check"><input type="checkbox" bind:checked={suggest} /> Filter bank list by UPI id</label>
      </div>
      <div class="grid-wrap">
        <Grid columnDefs={invCols} rowData={invoices} multiSelect={false} quickFilter={invSearch}
              on:selection={onInvoiceSelect} />
      </div>
    </div>

    <div class="pane">
      <h3>Unmatched bank credits ({entries.length})</h3>
      <div class="toolbar">
        <input type="text" placeholder="Search narration or amount" bind:value={bankSearch} />
      </div>
      <div class="grid-wrap">
        <Grid columnDefs={bankCols} rowData={entries} multiSelect={false} quickFilter={bankSearch}
              on:selection={(e) => (entrySel = e.detail[0] || null)} />
      </div>
    </div>
  </div>

  <div class="panel toolbar">
    {#if invSel && entrySel}
      <span>
        <b>{invSel.customer_name}</b> {invSel.month} ({inr(invSel.amount)}) ⇄ {inr(entrySel.credit)} on {entrySel.txn_date}
      </span>
      {#if amountMismatch}<span style="color:var(--unpaid)">Amounts differ — the invoice will be marked paid with the bank amount.</span>{/if}
    {:else}
      <span class="muted">Select one invoice and one bank credit to match them.</span>
    {/if}
    <span class="spacer"></span>
    <button class="btn primary" disabled={!invSel || !entrySel} on:click={match}>Match selected pair</button>
  </div>
</div>
