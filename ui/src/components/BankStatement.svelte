<script lang="ts">
  import { onMount } from 'svelte';
  import type { ColDef } from 'ag-grid-community';
  import Grid from './Grid.svelte';
  import { call } from '../lib/api';
  import { notify, inr } from '../lib/ui';
  import type { BankEntry, BankView, ImportResult } from '../lib/types';

  let rows: BankEntry[] = [];
  let view: BankView = 'ALL';
  let search = '';
  let lastImport: ImportResult | null = null;
  let busy = false;
  let fileInput: HTMLInputElement;

  const columns: ColDef<BankEntry>[] = [
    { field: 'txn_date', headerName: 'Date', width: 115, sort: 'desc' },
    { field: 'description', headerName: 'Narration', flex: 3, minWidth: 260, tooltipField: 'description' },
    { field: 'txn_ref', headerName: 'Reference', width: 160 },
    { field: 'debit', headerName: 'Debit', width: 120, type: 'rightAligned', valueFormatter: (p) => (p.value ? inr(p.value) : '') },
    { field: 'credit', headerName: 'Credit', width: 120, type: 'rightAligned', valueFormatter: (p) => (p.value ? inr(p.value) : '') },
    { field: 'balance', headerName: 'Balance', width: 130, type: 'rightAligned', valueFormatter: (p) => inr(p.value) },
    { field: 'matched_to', headerName: 'Matched to', flex: 1, minWidth: 160 }
  ];

  async function load() {
    try { rows = await call('list_bank_entries', view); } catch (e) { notify((e as Error).message, 'error'); }
  }
  onMount(load);
  $: view, load();

  // Send raw bytes so Python can read binary .xls as well as CSV text.
  function toBase64(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '');
      reader.onerror = () => reject(reader.error ?? new Error('Could not read the file'));
      reader.readAsDataURL(file);
    });
  }

  async function onFile(e: Event) {
    const file = (e.currentTarget as HTMLInputElement).files?.[0];
    if (!file) return;
    busy = true;
    try {
      lastImport = await call('import_bank_statement', file.name, await toBase64(file));
      notify(`${lastImport.inserted} new entries imported, ${lastImport.duplicates} already present`, 'success');
      await load();
    } catch (err) {
      notify((err as Error).message, 'error', 8000);
    } finally {
      busy = false;
      fileInput.value = '';
    }
  }
</script>

<div class="view">
  <div class="toolbar">
    <input bind:this={fileInput} type="file" accept=".csv,.txt,.xls" on:change={onFile} hidden />
    <button class="btn primary" disabled={busy} on:click={() => fileInput.click()}>
      {busy ? 'Importing…' : 'Upload statement (CSV / XLS)'}
    </button>
    <select bind:value={view}>
      <option value="ALL">All entries</option>
      <option value="UNMATCHED_CREDITS">Unmatched credits</option>
      <option value="MATCHED">Matched entries</option>
    </select>
    <span class="spacer"></span>
    <span class="count">{rows.length} entries</span>
    <input type="text" placeholder="Search narration, reference, amount" bind:value={search} />
  </div>

  {#if lastImport}
    <div class="panel">
      Last upload: <b>{lastImport.total}</b> rows read, <b>{lastImport.inserted}</b> new,
      <b>{lastImport.duplicates}</b> skipped as duplicates. Uploading the same file again changes nothing.
    </div>
  {/if}

  <div class="grid-wrap">
    <Grid columnDefs={columns} rowData={rows} quickFilter={search} multiSelect={false} />
  </div>
</div>
