<script lang="ts">
	import type { AccountApi } from '$lib/api/account';
	import { ApiFailure } from '$lib/api/errors';
	import { legend, markers } from '$lib/metrics/events';
	import { loadMetrics, METRICS, stepSeries, type MetricsData, type Window } from '$lib/metrics/series';
	import { clock } from '$lib/util/clock.svelte';
	import { fmtNum, mskDay, mskDayStart } from '$lib/util/format';
	import Page from '../shell/Page.svelte';
	import Chart from './Chart.svelte';

	type Period = 'today' | '7d' | '30d' | 'custom';

	interface Props {
		api: AccountApi;
		/** Фиксированный «сейчас» (тесты); без него — текущий момент и общий тикер. */
		now?: Date;
	}
	let { api, now }: Props = $props();
	let period = $state<Period>('today');
	let from = $state('');
	let to = $state('');
	let fields = $state<string[]>(['money', 'exp', 'motivation', 'stamina']);
	let data = $state<MetricsData | null>(null);
	let loading = $state(false);
	let error = $state('');
	let win = $state<Window | null>(null);
	const marks = $derived(data ? markers(data.events) : []);

	const DAY = 86_400_000;
	const PERIODS: { value: Period; label: string }[] = [
		{ value: 'today', label: 'сегодня' },
		{ value: '7d', label: '7 дней' },
		{ value: '30d', label: '30 дней' },
		{ value: 'custom', label: 'свой' }
	];

	// Сутки по МСК: окно «сегодня» пересчитывается после полуночи, а не каждую минуту.
	const today = $derived(mskDay(now ?? clock.now));

	/** Окно на момент выбора: конец — «сейчас» в этот момент. */
	function windowOf(p: Period): Window | null {
		const end = now ?? new Date();
		if (p === 'today') return { from: mskDayStart(end), to: end };
		if (p === '7d') return { from: new Date(end.getTime() - 7 * DAY), to: end };
		if (p === '30d') return { from: new Date(end.getTime() - 30 * DAY), to: end };
		if (!from || !to) return null;
		const a = new Date(`${from}T00:00:00+03:00`);
		const b = new Date(new Date(`${to}T00:00:00+03:00`).getTime() + DAY);
		return a < b ? { from: a, to: b } : null;
	}

	let progress = $state<{ pages: number; points: number } | null>(null);
	let request = 0;

	// Новый выбор отменяет прежнюю загрузку; её поздний ответ не применяется (номер запроса).
	$effect(() => {
		if (period === 'today') void today;
		const w = windowOf(period);
		const keys = [...fields];
		if (!w || keys.length === 0) return;
		const id = ++request;
		const ctl = new AbortController();
		loading = true;
		error = '';
		progress = null;
		loadMetrics(api, w, keys, {
			signal: ctl.signal,
			onProgress: (pages, points) => {
				if (id === request) progress = { pages, points };
			}
		})
			.then((d) => {
				if (id !== request) return;
				data = d;
				win = w;
			})
			.catch((e: unknown) => {
				if (id === request) error = e instanceof ApiFailure ? e.message : String(e);
			})
			.finally(() => {
				if (id !== request) return;
				loading = false;
				progress = null;
			});
		return () => ctl.abort();
	});

	function toggle(key: string) {
		fields = fields.includes(key) ? fields.filter((f) => f !== key) : [...fields, key];
	}

	function latest(key: string): string {
		const points = data?.series[key];
		const last = points?.at(-1) ?? data?.initial[key];
		return last ? fmtNum(last[1]) : '—';
	}
</script>

<Page title="Метрики">
	<div class="space-y-[14px]">
		<div class="flex flex-wrap items-center gap-1.5" role="group" aria-label="Период">
			{#each PERIODS as p (p.value)}
				<button type="button" class="chip" aria-pressed={period === p.value} onclick={() => (period = p.value)}
					>{p.label}</button
				>
			{/each}
			{#if period === 'custom'}
				<label class="chip gap-1 pr-1"
					>с <input type="date" class="bg-transparent text-xs focus:outline-none" bind:value={from} /></label
				>
				<label class="chip gap-1 pr-1"
					>по <input type="date" class="bg-transparent text-xs focus:outline-none" bind:value={to} /></label
				>
			{/if}
		</div>
		<div class="flex flex-wrap gap-1.5" role="group" aria-label="Поля">
			{#each METRICS as m (m.key)}
				<button type="button" class="chip" aria-pressed={fields.includes(m.key)} onclick={() => toggle(m.key)}
					>{m.label}</button
				>
			{/each}
		</div>
		{#if error}<p class="card ext-text text-sm text-bad-fg" role="alert">{error}</p>{/if}
		{#if loading}
			<p class="text-sm text-fg-muted" role="status">
				Загрузка…{#if progress}{` страниц: ${progress.pages}, точек: ${fmtNum(progress.points)}`}{/if}
			</p>
		{/if}
		{#if data && win}
			{#if marks.length}<p class="text-xs text-fg-muted">Метки на графиках: {legend(marks)}</p>{/if}
			<div class="grid gap-[14px] xl:grid-cols-2">
				{#each METRICS.filter((m) => fields.includes(m.key)) as m (m.key)}
					<div class="card min-w-0">
						<Chart
							label="{m.label} · {latest(m.key)}"
							name={m.label}
							data={stepSeries(data, m.key, win)}
							range={[win.from.getTime() / 1000, win.to.getTime() / 1000]}
							markers={marks}
						/>
					</div>
				{/each}
			</div>
		{/if}
	</div>
</Page>
