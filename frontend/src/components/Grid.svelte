<script>
  import { onMount, onDestroy, createEventDispatcher } from 'svelte';
  import { createGrid, ModuleRegistry, AllCommunityModule, themeQuartz } from 'ag-grid-community';

  ModuleRegistry.registerModules([AllCommunityModule]);

  export let columnDefs = [];
  export let rowData = [];
  export let multiSelect = true;
  export let quickFilter = '';

  const dispatch = createEventDispatcher();
  const theme = themeQuartz.withParams({
    accentColor: '#2b59c3',
    foregroundColor: '#14213d',
    headerBackgroundColor: '#e6ebf3',
    headerFontWeight: 600,
    fontFamily: '"Segoe UI", system-ui, sans-serif',
    fontSize: 13,
    spacing: 6,
    borderColor: '#d9dfe8',
    wrapperBorderRadius: 6
  });

  let el;
  let api;

  onMount(() => {
    api = createGrid(el, {
      theme,
      columnDefs,
      rowData,
      getRowId: (p) => String(p.data.id),
      defaultColDef: { sortable: true, filter: true, resizable: true, minWidth: 80 },
      rowSelection: multiSelect
        ? { mode: 'multiRow', checkboxes: true, headerCheckbox: true, enableClickSelection: false }
        : { mode: 'singleRow', checkboxes: false, enableClickSelection: true },
      singleClickEdit: true,
      stopEditingWhenCellsLoseFocus: true,
      animateRows: false,
      quickFilterText: quickFilter,
      onSelectionChanged: () => dispatch('selection', api.getSelectedRows()),
      onCellValueChanged: (e) => dispatch('cellchange', e)
    });
  });

  onDestroy(() => api && api.destroy());

  $: if (api) api.setGridOption('rowData', rowData);
  $: if (api) api.setGridOption('columnDefs', columnDefs);
  $: if (api) api.setGridOption('quickFilterText', quickFilter);
</script>

<div class="host" bind:this={el}></div>

<style>
  .host { height: 100%; width: 100%; }
</style>
