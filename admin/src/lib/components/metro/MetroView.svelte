<script lang="ts">
	import type { AccountApi } from '$lib/api/account';
	import { call } from '$lib/api/client';
	import { ApiFailure } from '$lib/api/errors';
	import type { MetroRunDetail, MetroRunSummary } from '$lib/api/types';
	import { OUTCOME_TEXT, outcomeOf, summarize } from '$lib/metro/model';
	import { clock } from '$lib/util/clock.svelte';
	import { fmtMoment, fmtSpan } from '$lib/util/format';
	import Pill from '../Pill.svelte';
	import MetroRun from './MetroRun.svelte';

	interface Props {
		api: AccountApi;
		/** Забег из адреса (`?run=`), иначе — последний. */
		initial?: number | null;
		onselect?: (id: number) => void;
		now?: Date;
	}
	let { api, initial = null, onselect, now: fixedNow }: Props = $props();
	const now = $derived(fixedNow ?? clock.now);
	let runs = $state<MetroRunSummary[]>([]);
	let next = $state<number | null>(null);
	let selected = $state<number | null>(null);
	let detail = $state<MetroRunDetail | null>(null);
	let error = $state('');
	let detailError = $state('');
	const stats = $derived(summarize(runs));

	async function loadRuns(before: number | null) {
		try {
			const page = await call(
				api.GET('/metro/runs', { params: { query: { limit: 20, ...(before ? { before } : {}) } } })
			);
			runs = before ? [...runs, ...page.items] : page.items;
			next = page.next_before;
			if (selected === null) selected = initial ?? runs[0]?.id ?? null;
		} catch (e) {
			error = e instanceof ApiFailure ? e.message : String(e);
		}
	}

	$effect(() => {
		void loadRuns(null);
	});

	$effect(() => {
		const id = selected;
		if (id === null) return;
		detail = null;
		detailError = '';
		// Ответ прежнего выбора — ни забег, ни ошибка — у нового не показывается.
		call(api.GET('/metro/runs/{run_id}', { params: { path: { run_id: id } } }))
			.then((d) => {
				if (selected === id) detail = d;
			})
			.catch((e: unknown) => {
				if (selected === id) detailError = e instanceof ApiFailure ? e.message : String(e);
			});
	});

	const pct = (share: number | null) => (share === null ? '—' : `${Math.round(share * 100)}%`);

	function pick(id: number) {
		selected = id;
		onselect?.(id);
	}
</script>

{#if error}<p class="card ext-text mb-3 text-sm text-bad-fg" role="alert">{error}</p>{/if}
<div class="grid gap-3 md:grid-cols-[16rem_minmax(0,1fr)]">
	<section class="card" aria-labelledby="runs-title">
		<h2 id="runs-title" class="card-title">Забеги</h2>
		<ul aria-label="Забеги метро">
			{#each runs as r (r.id)}
				{@const o = outcomeOf(r)}
				<li>
					<button
						type="button"
						class="flex w-full items-center justify-between gap-2 border-b border-line-soft px-1 py-1.5 text-left text-xs hover:bg-surface-2 {selected ===
						r.id
							? 'bg-accent-soft'
							: ''}"
						aria-pressed={selected === r.id}
						onclick={() => pick(r.id)}
					>
						<span>{fmtMoment(r.started_at, now)} · {r.steps} ш · {fmtSpan(r.duration_s)}</span>
						<Pill tone={o === 'self' ? 'ok' : o === 'ejected' ? 'warn' : 'bad'}>{OUTCOME_TEXT[o]}</Pill>
					</button>
				</li>
			{:else}
				<li class="py-1.5 text-sm text-fg-muted">Забегов ещё не было.</li>
			{/each}
		</ul>
		{#if next !== null}
			<button type="button" class="btn mt-2 w-full" onclick={() => void loadRuns(next)}>Ещё</button>
		{/if}
		{#if stats.total > 0}
			<dl class="mt-3 space-y-0.5 text-xs text-fg-muted" aria-label="Сводка забегов">
				<div>p90 длительности: {stats.p90DurationS !== null ? fmtSpan(stats.p90DurationS) : '—'}</div>
				<div>среднее время шага: {stats.meanStepS !== null ? `${stats.meanStepS.toFixed(1)} с` : '—'}</div>
				<div>шагов на клетку: {stats.stepsPerCell !== null ? stats.stepsPerCell.toFixed(1) : '—'}</div>
				<div>
					вышел сам {pct(stats.selfShare)} · выброс {pct(stats.ejectedShare)} · остановлен {pct(stats.stoppedShare)}
					<span class="text-fg-faint">({stats.self} / {stats.ejected} / {stats.stopped} из {stats.total})</span>
				</div>
			</dl>
		{/if}
	</section>
	<div class="card min-w-0">
		{#if detailError}
			<p class="ext-text text-sm text-bad-fg" role="alert">{detailError}</p>
		{:else if detail}
			<MetroRun run={detail} {now} />
		{:else if selected !== null}
			<p class="text-sm text-fg-muted" role="status">Загрузка забега #{selected}…</p>
		{:else}
			<p class="text-sm text-fg-muted">Выберите забег.</p>
		{/if}
	</div>
</div>
