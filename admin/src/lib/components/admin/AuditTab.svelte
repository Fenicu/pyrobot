<script lang="ts">
	import { onMount } from 'svelte';
	import type { AdminStore } from '$lib/admin/store.svelte';
	import { fmtMoment } from '$lib/util/format';

	interface Props {
		store: AdminStore;
	}

	let { store }: Props = $props();

	onMount(() => {
		if (store.audit.length === 0) {
			void store.loadAudit();
		}
	});

	function formatDetails(details: Record<string, unknown> | undefined): string {
		if (!details || Object.keys(details).length === 0) return '—';
		return JSON.stringify(details);
	}
</script>

<div class="space-y-[14px]">
	{#if store.auditError}
		<p class="card text-sm text-bad-fg" role="alert">{store.auditError}</p>
	{/if}

	{#if store.auditLoading && store.audit.length === 0}
		<p class="text-sm text-fg-muted" role="status">Загрузка журнала действий…</p>
	{:else if store.audit.length === 0}
		<p class="text-sm text-fg-muted">Записей журнала нет.</p>
	{:else}
		<div
			role="table"
			aria-label="Журнал действий"
			class="space-y-2 xl:space-y-0 xl:overflow-hidden xl:rounded-lg xl:border xl:border-line xl:bg-surface"
		>
			<div
				role="row"
				class="hidden gap-3 border-b border-line-soft px-3 py-2 text-xs font-semibold tracking-wide text-fg-muted uppercase xl:grid xl:grid-cols-[minmax(0,1fr)_minmax(0,0.8fr)_minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,2fr)]"
			>
				<span role="columnheader">Время</span>
				<span role="columnheader">Кто</span>
				<span role="columnheader">Действие</span>
				<span role="columnheader">Цель</span>
				<span role="columnheader">Детали</span>
			</div>

			{#each store.audit as item (item.id)}
				<div
					role="row"
					class="card grid gap-1.5 xl:grid-cols-[minmax(0,1fr)_minmax(0,0.8fr)_minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,2fr)] xl:items-center xl:gap-3 xl:rounded-none xl:border-0 xl:border-b xl:border-line-soft xl:last:border-b-0"
				>
					<div role="cell" class="text-xs text-fg-muted">
						<span class="xl:hidden">Время: </span>
						{fmtMoment(item.at)}
					</div>

					<div role="cell" class="min-w-0 font-mono text-xs font-medium [overflow-wrap:anywhere]">
						<span class="text-fg-muted xl:hidden">Кто: </span>
						{item.actor_login}
					</div>

					<div role="cell" class="min-w-0 font-mono text-xs text-accent [overflow-wrap:anywhere]">
						<span class="text-fg-muted xl:hidden">Действие: </span>
						{item.action}
					</div>

					<div role="cell" class="text-xs text-fg-muted">
						<span class="xl:hidden">Цель: </span>
						{item.target_type ?? '—'}{item.target_id !== null ? ` #${item.target_id}` : ''}
					</div>

					<div role="cell" class="min-w-0 font-mono text-[11px] text-fg-faint truncate" title={formatDetails(item.details)}>
						<span class="text-fg-muted xl:hidden">Детали: </span>
						{formatDetails(item.details)}
					</div>
				</div>
			{/each}
		</div>

		{#if store.auditNextBefore !== null}
			<div class="pt-2">
				<button
					type="button"
					class="btn w-full"
					disabled={store.auditLoading}
					onclick={() => void store.loadAudit(true)}
				>
					{store.auditLoading ? 'Загрузка…' : 'Ещё'}
				</button>
			</div>
		{/if}
	{/if}
</div>
