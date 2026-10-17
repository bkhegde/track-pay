<script lang="ts">
  import { onMount } from 'svelte';
  import type { CellValueChangedEvent, ColDef, ICellRendererParams } from 'ag-grid-community';
  import Grid from './Grid.svelte';
  import { call } from '../lib/api';
  import { notify, ask, inr } from '../lib/ui';
  import type { Customer, CustomerInput } from '../lib/types';

  let rows: Customer[] = [];
  let selected: Customer[] = [];
  let search = '';
  let showForm = false;
  let form: CustomerInput = blank();

  $: ids = selected.map((r) => r.id);

  const columns: ColDef<Customer>[] = [
    { field: 'name', headerName: 'Name', editable: true, flex: 1.3, minWidth: 160 },
    { field: 'phone', headerName: 'Phone', editable: true, width: 150 },
    {
      field: 'monthly_fee', headerName: 'Monthly fee', editable: true, width: 140, type: 'rightAligned',
      cellEditor: 'agNumberCellEditor', cellEditorParams: { min: 0 },
      valueFormatter: (p) => inr(p.value), valueParser: (p) => Number(p.newValue)
    },
    { field: 'upi_ids', headerName: 'UPI ids (comma separated)', editable: true, flex: 2, minWidth: 220 },
    { field: 'active', headerName: 'Active', editable: true, width: 100, cellDataType: 'boolean' },
    {
      headerName: 'Status', width: 120, valueGetter: (p) => (p.data?.active ? 'Active' : 'Inactive'),
      cellRenderer: (p: ICellRendererParams<Customer, string>) => `<span class="badge ${p.value === 'Active' ? 'paid' : 'off'}">${p.value}</span>`
    }
  ];

  function blank(): CustomerInput {
    return { id: null, name: '', phone: '', monthly_fee: 0, upi_ids: '', active: true };
  }

  async function load() {
    try { rows = await call('list_customers'); } catch (e) { notify((e as Error).message, 'error'); }
  }
  onMount(load);

  async function onCellChange(e: CustomEvent<CellValueChangedEvent<Customer>>) {
    try {
      await call('save_customer', { ...e.detail.data });
      notify('Saved', 'success', 1500);
    } catch (err) {
      notify((err as Error).message, 'error');
    }
    await load(); // refresh (also reverts a rejected edit)
  }

  function openAdd() { form = blank(); showForm = true; }

  async function saveForm() {
    try {
      await call('save_customer', { ...form });
      showForm = false;
      notify('Customer added', 'success');
      await load();
    } catch (e) { notify((e as Error).message, 'error'); }
  }

  async function setActive(active: boolean) {
    try {
      const r = await call('set_customers_active', ids, active);
      notify(`${r.updated} customer(s) ${active ? 'activated' : 'deactivated'}`, 'success');
      await load();
    } catch (e) { notify((e as Error).message, 'error'); }
  }

  async function remove() {
    if (!(await ask(`Delete ${ids.length} selected customer(s)? This cannot be undone.`, 'Delete'))) return;
    try {
      const r = await call('delete_customers', ids);
      if (r.deleted) notify(`${r.deleted} customer(s) deleted`, 'success');
      if (r.skipped.length)
        notify(`Not deleted (they have invoices — deactivate instead): ${r.skipped.join(', ')}`, 'error', 8000);
      await load();
    } catch (e) { notify((e as Error).message, 'error'); }
  }
</script>

<div class="view">
  <div class="toolbar">
    <button class="btn primary" on:click={openAdd}>Add customer</button>
    <span class="sep"></span>
    <button class="btn" disabled={!ids.length} on:click={() => setActive(true)}>Activate selected</button>
    <button class="btn" disabled={!ids.length} on:click={() => setActive(false)}>Deactivate selected</button>
    <button class="btn danger" disabled={!ids.length} on:click={remove}>Delete selected</button>
    <span class="spacer"></span>
    <span class="count">{ids.length} of {rows.length} selected</span>
    <input type="text" placeholder="Search customers" bind:value={search} />
  </div>
  <div class="grid-wrap">
    <Grid columnDefs={columns} rowData={rows} quickFilter={search}
          on:selection={(e) => (selected = e.detail)} on:cellchange={onCellChange} />
  </div>
</div>

{#if showForm}
  <div class="backdrop">
    <div class="modal" role="dialog" aria-modal="true">
      <h3>Add customer</h3>
      <label class="row"><span>Name</span><input type="text" bind:value={form.name} /></label>
      <label class="row"><span>Phone</span><input type="text" bind:value={form.phone} /></label>
      <label class="row"><span>Monthly fee (₹)</span><input type="number" min="0" step="0.01" bind:value={form.monthly_fee} /></label>
      <label class="row">
        <span>UPI ids</span>
        <textarea rows="2" bind:value={form.upi_ids} placeholder="name@okhdfcbank, 98765xxxxx@ybl"></textarea>
        <span class="hint">Separate several ids with commas. Used to match bank payments.</span>
      </label>
      <label class="check"><input type="checkbox" bind:checked={form.active} /> Active</label>
      <div class="actions">
        <button class="btn" on:click={() => (showForm = false)}>Cancel</button>
        <button class="btn primary" on:click={saveForm}>Save customer</button>
      </div>
    </div>
  </div>
{/if}
