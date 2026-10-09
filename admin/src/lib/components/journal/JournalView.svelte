<script lang="ts">
	import type { AccountApi } from '$lib/api/account';
	import { errorText } from '$lib/api/errors';
	import type { JournalItem } from '$lib/api/types';
	import type { Confirmer } from '$lib/commands';
	import { dayLabel } from '$lib/daily/text';
	import { chronicle, type ChronicleRow } from '$lib/journal/chronicle';
	import type { LiveEvent } from '$lib/live/sse';
	import { keyOf, type JournalFeed } from '$lib/stores/journal.svelte';
	import { clock } from '$lib/util/clock.svelte';
	import { mskDay } from '$lib/util/format';
	import { media, WIDE } from '$lib/util/media.svelte';
	import Modal from '../Modal.svelte';
	import Page from '../shell/Page.svelte';
	import FeedFilters from './FeedFilters.svelte';
	import FeedRow from './FeedRow.svelte';
	import JournalDetail from './JournalDetail.svelte';
	import RunRow from './RunRow.svelte';

	interface Props {
		api: AccountApi;
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
	// Панель разбора — рядом с лентой, где строкам запусков ещё хватает ширины; уже — шторкой.
	const wide = media(WIDE);

	// Выбранная строка следит за обновлениями ленты (статус действия из SSE).
	const current = $derived(
		selected ? (feed.items.find((i) => keyOf(i) === keyOf(selected!)) ?? selected) : null
	);

	// Хроника — в ленте «все»: запуск сценария (решение, шаги, ответы игры) — одна строка, раскрывается
	// по клику. С фильтром по типу, статусу или источнику — плоская лента, запись за записью.
	const grouped = $derived(
		feed.filter.type === null && feed.filter.status === null && feed.filter.source === null
	);
	const rows = $derived<ChronicleRow[]>(
		grouped ? chronicle(feed.items) : feed.items.map((item) => ({ kind: 'item', key: keyOf(item), item }))
	);
	// Сутки по МСК: над первой строкой каждых суток — «Сегодня, 27.09» или «26.09 сб».
	const dayOf = (row: ChronicleRow) => mskDay(new Date(row.kind === 'run' ? row.started : row.item.at));
	const days = $derived(rows.map((row, i) => (i === 0 || dayOf(rows[i - 1]!) !== dayOf(row) ? dayOf(row) : null)));
	let open = $state<string[]>([]);
	const toggle = (key: string) => (open = open.includes(key) ? open.filter((k) => k !== key) : [...open, key]);
	const selectedKey = $derived(current ? keyOf(current) : null);

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

{#snippet actions()}
	<button
		type="button"
		class="chip"
		aria-pressed={feed.live}
		title="Новые записи сверху, по мере прихода"
		onclick={() => {
			feed.live = !feed.live;
			if (feed.live && feed.missed > 0) void feed.reload();
		}}>⏵ live</button
	>
{/snippet}

<Page title="Журнал" {actions}>
	<div class="space-y-[14px]">
		<FeedFilters filter={feed.filter} onchange={(next) => feed.setFilter(next)} />
		{#if !feed.live && feed.missed > 0}
			<button type="button" class="btn w-full" onclick={() => void feed.reload()}>
				Новых записей: {feed.missed} — показать
			</button>
		{/if}
		{#if feed.error}
			<p class="card ext-text text-sm text-bad-fg" role="alert">{errorText(feed.error)}</p>
		{/if}

		<div class="xl:flex xl:items-start xl:gap-[14px]">
			<section class="card min-w-0 overflow-hidden p-0 xl:flex-1" aria-label="Лента">
				{#if feed.items.length === 0 && !feed.loading}
					<p class="p-3 text-sm text-fg-muted">Записей нет.</p>
				{/if}
				<ul>
					{#each rows as row, i (row.key)}
						{#if days[i]}
							<li class="border-b border-line-soft bg-surface-2 px-2 py-1 text-xs font-semibold text-fg-muted">
								<h3>{dayLabel(days[i]!, mskDay(now))}</h3>
							</li>
						{/if}
						{#if row.kind === 'run'}
							<RunRow
								group={row}
								{now}
								expanded={open.includes(row.key)}
								selected={selectedKey}
								ontoggle={() => toggle(row.key)}
								onselect={(i) => (selected = i)}
							/>
						{:else}
							<FeedRow
								item={row.item}
								{now}
								selected={selectedKey === row.key}
								onselect={(i) => (selected = i)}
							/>
						{/if}
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

			{#if wide.current}
				<aside
					class="card sticky top-3.5 max-h-[calc(100dvh-1.75rem)] w-[420px] shrink-0 overflow-y-auto"
					aria-label="Разбор"
				>
					{#if current}
						<JournalDetail {api} item={current} {subscribe} {confirmer} onstale={() => void feed.reload()} />
					{:else}
						<p class="text-sm text-fg-muted">Выберите запись — здесь будет разбор.</p>
					{/if}
				</aside>
			{/if}
		</div>
	</div>
</Page>

{#if !wide.current && current}
	<Modal title={title(current)} variant="sheet" onclose={() => (selected = null)}>
		<JournalDetail {api} item={current} {subscribe} {confirmer} onstale={() => void feed.reload()} />
	</Modal>
{/if}
