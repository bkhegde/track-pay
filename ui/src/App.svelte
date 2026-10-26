<script lang="ts">
  import Customers from './components/Customers.svelte';
  import Invoices from './components/Invoices.svelte';
  import BankStatement from './components/BankStatement.svelte';
  import Reconcile from './components/Reconcile.svelte';
  import { call } from './lib/api';
  import { toasts, confirmState, notify } from './lib/ui';

  type TabId = 'customers' | 'invoices' | 'bank' | 'reconcile';

  const tabs: { id: TabId; label: string }[] = [
    { id: 'customers', label: 'Customers' },
    { id: 'invoices', label: 'Invoices' },
    { id: 'bank', label: 'Bank statement' },
    { id: 'reconcile', label: 'Reconcile' }
  ];
  let tab: TabId = 'customers';

  let backingUp = false;
  async function backup() {
    backingUp = true;
    try {
      const { path } = await call('backup_database');
      notify(`Backup saved: ${path}`, 'success', 8000);
    } catch (e) {
      notify((e as Error).message, 'error', 8000);
    } finally {
      backingUp = false;
    }
  }

  function answer(result: boolean) {
    $confirmState?.resolve(result);
    confirmState.set(null);
  }
</script>

<div class="shell">
  <header class="topbar">
    <div class="brand">Track-Pay</div>
    <nav class="tabs">
      {#each tabs as t}
        <button class="tab" class:active={tab === t.id} on:click={() => (tab = t.id)}>{t.label}</button>
      {/each}
    </nav>
    <button class="btn backup" disabled={backingUp} on:click={backup}>{backingUp ? 'Backing up…' : 'Backup'}</button>
  </header>

  <main>
    {#if tab === 'customers'}<Customers />{/if}
    {#if tab === 'invoices'}<Invoices />{/if}
    {#if tab === 'bank'}<BankStatement />{/if}
    {#if tab === 'reconcile'}<Reconcile />{/if}
  </main>
</div>

<div class="toasts" aria-live="polite">
  {#each $toasts as t (t.id)}
    <div class="toast {t.type}">{t.message}</div>
  {/each}
</div>

{#if $confirmState}
  <div class="backdrop">
    <div class="modal" role="dialog" aria-modal="true">
      <p>{$confirmState.message}</p>
      <div class="actions">
        <button class="btn" on:click={() => answer(false)}>Cancel</button>
        <button class="btn primary" on:click={() => answer(true)}>{$confirmState.okLabel}</button>
      </div>
    </div>
  </div>
{/if}
