<script lang="ts">
	import type { AccountOut, FeedType } from '$lib/api/types';
	import { FEED_TYPES, type FeedFilters } from '$lib/overview/feed';
	import { accountTitle } from '$lib/util/game';

	interface Props {
		accounts: AccountOut[] | null;
		value: FeedFilters;
		onchange: (value: FeedFilters) => void;
	}
	let { accounts, value, onchange }: Props = $props();

	const toggle = <T,>(list: T[], item: T) => (list.includes(item) ? list.filter((x) => x !== item) : [...list, item]);
	const total = $derived(accounts?.length ?? 0);
	const label = $derived(value.accountIds.length === 0 ? 'Аккаунты: все' : `Аккаунты: ${value.accountIds.length} из ${total}`);
	const PILL = 'chip';
	const ON = 'border-accent bg-accent-soft text-accent';

	const pickType = (t: FeedType) => onchange({ ...value, types: toggle(value.types, t) });
</script>

<div class="space-y-2">
	<div class="flex flex-wrap items-center gap-1" role="group" aria-label="Типы событий">
		<button type="button" class="{PILL} {value.types.length === 0 ? ON : ''}" aria-pressed={value.types.length === 0} onclick={() => onchange({ ...value, types: [] })}>Все</button>
		{#each FEED_TYPES as t (t.type)}
			{@const on = value.types.includes(t.type)}
			<button type="button" class="{PILL} {on ? ON : ''}" aria-pressed={on} onclick={() => pickType(t.type)}>{t.label}</button>
		{/each}
	</div>
	{#if accounts && accounts.length > 0}
		<details class="rounded-card border border-line bg-surface">
			<summary class="cursor-pointer px-3 py-2 text-sm">{label}</summary>
			<div class="max-h-64 space-y-1 overflow-y-auto border-t border-line-soft px-3 py-2">
				<button type="button" class="btn btn-ghost" onclick={() => onchange({ ...value, accountIds: [] })}>Все аккаунты</button>
				{#each accounts as a (a.id)}
					<label class="flex min-h-8 items-center gap-2 text-sm">
						<input
							type="checkbox"
							checked={value.accountIds.includes(a.id)}
							onchange={() => onchange({ ...value, accountIds: toggle(value.accountIds, a.id) })}
						/>
						<span class="ext-text min-w-0">{accountTitle(a)}</span>
					</label>
				{/each}
			</div>
		</details>
	{/if}
</div>
