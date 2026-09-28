<script lang="ts">
	import type { Api } from '$lib/api/client';
	import { errorText } from '$lib/api/errors';
	import type { JournalItem } from '$lib/api/types';
	import type { Confirmer } from '$lib/commands';
	import type { LiveEvent } from '$lib/live/sse';
	import { keyOf, type JournalFeed } from '$lib/stores/journal.svelte';
	import { clock } from '$lib/util/clock.svelte';
	import { DESKTOP, media } from '$lib/util/media.svelte';
	import Modal from '../Modal.svelte';
	import FeedFilters from './FeedFilters.svelte';
	import FeedRow from './FeedRow.svelte';
	import JournalDetail from './JournalDetail.svelte';

	interface Props {
		api: Api;
		feed: JournalFeed;
		subscribe?: (handler: (e: LiveEvent) => void) => () => void;
		confirmer?: Confirmer;
		/** Момент «сейчас» для дат строк (сегодняшние — без даты); без него — общий тикер. */
		now?: Date;
	}
	let { api, feed, subscribe, confirmer, now: fixedNow }: Props = $props();
	const now = $derived(fixedNow ?? clock.now);
	let selected = $state<JournalItem | null>(null);
	let sentinel = $state<HTMLElement>();
	const desktop = media(DESKTOP);

	// Выбранная строка следит за обновлениями ленты (статус действия из SSE).
	const current = $derived(
		selected ? (feed.items.find((i) => keyOf(i) === keyOf(selected!)) ?? selected) : null
	);

	$effect(() => {
		if (!sentinel || typeof IntersectionObserver === 'undefined') return;
		const io = new IntersectionObserver((entries) => {
			if (entries.some((e) => e.isIntersecting)) void feed.more();
		});
		io.observe(sentinel);
		return () => io.disconnect();
	});

	function title(item: JournalItem): string {
		return item.type === 'decision' ? 'Решение' : item.type === 'action' ? 'Действие' : 'Сообщение';
	}
</script>

<div class="space-y-3">
	<FeedFilters
		filter={feed.filter}
		live={feed.live}
		onchange={(next) => feed.setFilter(next)}
		onlive={(on) => {
			feed.live = on;
			if (on && feed.missed > 0) void feed.reload();
		}}
	/>
	{#if !feed.live && feed.missed > 0}
		<button type="button" class="btn w-full" onclick={() => void feed.reload()}>
			Новых записей: {feed.missed} — показать
		</button>
	{/if}
	{#if feed.error}
		<p class="card ext-text text-sm text-bad-fg" role="alert">{errorText(feed.error)}</p>
	{/if}

	<div class="md:grid md:grid-cols-[minmax(0,1fr)_24rem] md:gap-4">
		<section class="card p-0" aria-label="Лента">
			{#if feed.items.length === 0 && !feed.loading}
				<p class="p-3 text-sm text-fg-muted">Записей нет.</p>
			{/if}
			<ul>
				{#each feed.items as item (keyOf(item))}
					<FeedRow
						{item}
						{now}
						selected={current !== null && keyOf(current) === keyOf(item)}
						onselect={(i) => (selected = i)}
					/>
				{/each}
			</ul>
			<div bind:this={sentinel} class="p-2 text-center">
				{#if feed.loading}
					<span class="text-sm text-fg-muted" role="status">Загрузка…</span>
				{:else if !feed.done}
					<button type="button" class="btn" onclick={() => void feed.more()}>Показать ещё</button>
				{:else if feed.items.length > 0}
					<span class="text-xs text-fg-faint">Это всё.</span>
				{/if}
			</div>
		</section>

		{#if desktop.current}
			<aside class="card sticky top-4 max-h-[calc(100dvh-2rem)] self-start overflow-y-auto" aria-label="Разбор">
				{#if current}
					<JournalDetail {api} item={current} {subscribe} {confirmer} onstale={() => void feed.reload()} />
				{:else}
					<p class="text-sm text-fg-muted">Выберите запись — здесь будет разбор.</p>
				{/if}
			</aside>
		{/if}
	</div>
</div>

{#if !desktop.current && current}
	<Modal title={title(current)} variant="sheet" onclose={() => (selected = null)}>
		<JournalDetail {api} item={current} {subscribe} {confirmer} onstale={() => void feed.reload()} />
	</Modal>
{/if}
