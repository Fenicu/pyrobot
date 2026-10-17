<script lang="ts">
	import type { OverviewTotals } from '$lib/api/types';
	import { readPalette } from '$lib/metrics/chart';
	import { init, type ECharts } from '$lib/metrics/echarts';
	import { chartSeries, METRICS, type MetricKey } from '$lib/overview/totals';
	import { barOption } from '$lib/overview/totals-chart';

	interface Props {
		data: OverviewTotals;
		nameOf: (id: number) => string;
	}
	let { data, nameOf }: Props = $props();

	let metric = $state<MetricKey>('money');
	let box = $state<HTMLDivElement>();
	let chart = $state.raw<ECharts | null>(null);
	// Перекраска при смене темы (класс .light на <html>).
	let theme = $state(0);

	const chartData = $derived(chartSeries(data, metric, nameOf));
	const current = $derived(METRICS.find((m) => m.key === metric)!);
	const empty = $derived(chartData.series.length === 0);

	$effect(() => {
		const el = box;
		if (!el) return;
		const c = init(el);
		chart = c;
		const resize = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => c.resize());
		resize?.observe(el);
		const recolor = new MutationObserver(() => theme++);
		recolor.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
		return () => {
			resize?.disconnect();
			recolor.disconnect();
			c.dispose();
			chart = null;
		};
	});

	$effect(() => {
		const c = chart;
		if (!c) return;
		void theme;
		c.setOption(barOption(chartData, metric, readPalette(), document.documentElement.classList.contains('light')), { notMerge: true });
	});
</script>

<section class="card min-w-0 space-y-2" aria-label="График по дням">
	<div class="flex flex-wrap items-center gap-1" role="group" aria-label="Показатель">
		{#each METRICS as m (m.key)}
			<button
				type="button"
				class="chip {metric === m.key ? 'border-accent bg-accent-soft text-accent' : ''}"
				aria-pressed={metric === m.key}
				onclick={() => (metric = m.key)}>{m.icon} {m.title}</button
			>
		{/each}
	</div>
	{#if empty}
		<p class="rounded-md bg-surface-2 p-3 text-xs text-fg-faint">Нет данных за период.</p>
	{:else}
		<div
			bind:this={box}
			class="h-64 w-full md:h-80"
			role="img"
			aria-label="График: {current.icon} {current.title} по дням, аккаунтов {chartData.series.length}"
		></div>
		<p class="text-xs text-fg-faint">
			Штриховка - неполные сутки (сегодня и дни до запуска журнала).
		</p>
		<p class="text-xs text-fg-faint">сумма по дням может не сходиться с итогом периода: в дни без изменения значения точек нет</p>
	{/if}
</section>
