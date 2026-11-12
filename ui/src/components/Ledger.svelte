<script lang="ts">
  import { onMount } from 'svelte';
  import type { ColDef, ICellRendererParams } from 'ag-grid-community';
  import Grid from './Grid.svelte';
  import { call } from '../lib/api';
  import { notify, ask, inr } from '../lib/ui';
  import type { Customer, Transaction } from '../lib/types';

  let customers: Customer[] = [];
  let customerId: number | null = null;
  let rows: Transaction[] = [];
  let selected: Transaction[] = [];
  let search = '';

  $: payments = selected.filter((r) => r.kind !== 'INVOICE');
  $: balance = customerId != null && rows.length ? rows[0].balance : null; // newest row first

  const KIND: Record<string, string> = { INVOICE: 'Invoice', BANK: 'Bank', CASH: 'Cash' };

  const columns: ColDef<Transaction>[] = [
    { field: 'txn_date', headerName: 'Date', width: 115, sort: 'desc' },
    { field: 'customer_name', headerName: 'Customer', flex: 1, minWidth: 150 },
    {
      field: 'kind', headerName: 'Type', width: 105,
      valueFormatter: (p) => KIND[p.value as string] ?? p.value
    },
    { field: 'description', headerName: 'Description', flex: 2, minWidth: 220, tooltipField: 'description' },
    { field: 'invoice_month', headerName: 'Applies to', width: 115 },
    { field: 'txn_ref', headerName: 'Reference', width: 160 },
    {
      field: 'amount', headerName: 'Amount', width: 130, type: 'rightAligned',
      cellRenderer: (p: ICellRendererParams<Transaction, number>) =>
        `<span style="color:${(p.value ?? 0) < 0 ? 'var(--unpaid)' : 'var(--paid)'}">${inr(p.value)}</span>`
    },
    { field: 'balance', headerName: 'Balance', width: 130, type: 'rightAligned', valueFormatter: (p) => inr(p.value) }
  ];

  async function load() {
    try {
      rows = await call('list_transactions', customerId);
      selected = [];
    } catch (e) { notify((e as Error).message, 'error'); }
  }
  onMount(async () => {
    try { customers = await call('list_customers'); } catch (e) { notify((e as Error).message, 'error'); }
  });
  $: customerId, load();

  async function removePayments() {
    if (!(await ask(`Remove ${payments.length} payment(s)? Invoices they settled become outstanding again and bank entries are released.`, 'Remove'))) return;
    try {
      await call('delete_payments', payments.map((r) => r.id));
      await load();
    } catch (e) { notify((e as Error).message, 'error'); }
  }
</script>

<div class="view">
  <div class="toolbar">
    <label>Customer
      <select bind:value={customerId}>
        <option value={null}>All customers</option>
        {#each customers as c (c.id)}<option value={c.id}>{c.name}</option>{/each}
      </select>
    </label>
    <button class="btn" disabled={!payments.length} on:click={removePayments}>Remove selected payments</button>
    <span class="spacer"></span>
    {#if balance != null}
      <span class="count">{balance < 0 ? `Owes ${inr(-balance)}` : balance > 0 ? `Advance ${inr(balance)}` : 'Settled'}</span>
    {/if}
    <span class="count">{rows.length} postings</span>
    <input type="text" placeholder="Search" bind:value={search} />
  </div>

  <div class="panel">
    Every invoice is posted as a negative amount and every payment (bank match or cash) as a positive amount.
    Invoices are settled from a customer's payments, oldest month first. Only payments can be removed here;
    change an invoice amount on the Invoices tab.
  </div>

  <div class="grid-wrap">
    <Grid columnDefs={columns} rowData={rows} quickFilter={search}
          on:selection={(e) => (selected = e.detail)} />
  </div>
</div>
