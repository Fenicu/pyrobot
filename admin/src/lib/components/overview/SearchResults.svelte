<script lang="ts">
	import type { SearchHitOut } from '$lib/api/types';
	import { groupHits } from '$lib/overview/search';
	import SearchHitItem from './SearchHitItem.svelte';

	interface Props {
		hits: SearchHitOut[];
		accountsMap: Map<number, string>;
		now?: Date;
	}
	let { hits, accountsMap, now = new Date() }: Props = $props();

	let viewMode = $state<'grouped' | 'flat'>('grouped');

	const groups = $derived(groupHits(hits));
	const sortedFlat = $derived([...hits].sort((a, b) => Date.parse(b.date) - Date.parse(a.date)));
</script>

{#if hits.length === 0}
	<p class="card text-sm text-fg-muted">Ничего не найдено.</p>
{:else}
	<div class="space-y-2.5">
		<div class="flex items-center justify-between gap-2 px-0.5">
			<span class="text-xs text-fg-muted">
				Найдено: <strong class="text-fg tabular-nums">{hits.length}</strong>
				{hits.length === 1 ? 'сообщение' : 'сообщений'}
			</span>
			<button
				type="button"
				class="btn text-xs"
				onclick={() => (viewMode = viewMode === 'grouped' ? 'flat' : 'grouped')}
			>
				{viewMode === 'grouped' ? 'Списком' : 'По группам'}
			</button>
		</div>

		{#if viewMode === 'grouped'}
			<div class="space-y-2" role="region" aria-label="Результаты по группам">
				{#each groups as group (group.key)}
					<details class="group rounded-card border border-line bg-surface overflow-hidden" open>
						<summary class="flex cursor-pointer items-center justify-between gap-2 px-3 py-2 text-sm transition-colors hover:bg-surface-2">
							<div class="flex min-w-0 items-center gap-2">
								<span class="pill pill-muted shrink-0 tabular-nums">{group.count}</span>
								<span class="ext-text min-w-0 text-xs font-medium md:text-sm">{group.sample}</span>
							</div>
						</summary>
						<div class="space-y-2 border-t border-line-soft bg-bg/50 p-2.5">
							{#each group.hits as hit (`${hit.account_id}:${hit.msg_id}`)}
								<SearchHitItem
									{hit}
									accountName={accountsMap.get(hit.account_id) ?? `#${hit.account_id}`}
									{now}
								/>
							{/each}
						</div>
					</details>
				{/each}
			</div>
		{:else}
			<div class="space-y-2" role="region" aria-label="Результаты списком">
				{#each sortedFlat as hit (`${hit.account_id}:${hit.msg_id}`)}
					<SearchHitItem
						{hit}
						accountName={accountsMap.get(hit.account_id) ?? `#${hit.account_id}`}
						{now}
					/>
				{/each}
			</div>
		{/if}
	</div>
{/if}
