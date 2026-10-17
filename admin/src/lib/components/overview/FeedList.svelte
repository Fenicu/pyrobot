<script lang="ts">
	import type { AccountOut, FeedItem } from '$lib/api/types';
	import { accountDotLabel, accountTone } from '$lib/accounts/status';
	import { accountHref } from '$lib/nav';
	import { feedIcon, feedText } from '$lib/overview/feed';
	import { fmtMoment } from '$lib/util/format';
	import { accountTitle } from '$lib/util/game';
	import StatusDot from '../ui/StatusDot.svelte';

	interface Props {
		items: FeedItem[];
		/** Список аккаунтов за именами; null - ещё не пришёл (подпись «#id»). */
		accounts: AccountOut[] | null;
		now: Date;
	}
	let { items, accounts, now }: Props = $props();

	const byId = $derived(new Map((accounts ?? []).map((a) => [a.id, a])));
</script>

{#if items.length === 0}
	<p class="card text-sm text-fg-muted">Событий пока нет.</p>
{:else}
	<ul class="overflow-hidden rounded-card border border-line bg-surface text-sm" aria-label="События">
		{#each items as item (item.key)}
			{@const a = byId.get(item.account_id)}
			<!-- Телефон: время и аккаунт первым рядом, событие вторым; от md - одна строка. -->
			<li class="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5 border-b border-line-soft px-3 py-2 last:border-0 md:flex-nowrap">
				<time class="w-24 shrink-0 text-fg-muted tabular-nums" datetime={item.at}>{fmtMoment(item.at, now)}</time>
				<span class="flex min-w-0 items-center gap-1.5 md:w-44 md:shrink-0">
					{#if a}<StatusDot tone={accountTone(a)} label={accountDotLabel(a)} />{/if}
					<a href={accountHref(item.account_id, '')} class="ext-text min-w-0 font-medium hover:underline">{a ? accountTitle(a) : `#${item.account_id}`}</a>
				</span>
				<span class="flex min-w-0 basis-full gap-1.5 md:basis-auto md:flex-1">
					<span aria-hidden="true" class="shrink-0">{feedIcon(item)}</span>
					<span class="min-w-0 break-words {item.type === 'alert' && item.level === 'error' ? 'text-bad-fg' : ''}">{feedText(item)}</span>
				</span>
			</li>
		{/each}
	</ul>
{/if}
