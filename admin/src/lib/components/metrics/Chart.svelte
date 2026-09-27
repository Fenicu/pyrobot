<script lang="ts">
	import uPlot from 'uplot';
	import 'uplot/dist/uPlot.min.css';
	import { TZ } from '$lib/util/format';

	interface Props {
		label: string;
		data: [number[], number[]];
		height?: number;
	}
	let { label, data, height = 140 }: Props = $props();
	let box = $state<HTMLDivElement>();
	let chart: uPlot | null = null;

	function css(name: string, fallback: string): string {
		return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
	}

	function options(width: number): uPlot.Options {
		const axis = { stroke: css('--fg-muted', '#999'), grid: { stroke: css('--line-soft', '#333'), width: 1 } };
		return {
			width,
			height,
			legend: { show: false },
			cursor: { drag: { x: true, y: false } },
			tzDate: (ts) => uPlot.tzDate(new Date(ts * 1e3), TZ),
			scales: { x: { time: true } },
			axes: [axis, { ...axis, size: 70 }],
			series: [
				{},
				{
					label,
					stroke: css('--accent', '#4f8cff'),
					width: 2,
					paths: uPlot.paths.stepped?.({ align: 1 }),
					points: { show: false }
				}
			]
		};
	}

	$effect(() => {
		const el = box;
		if (!el) return;
		const current = data;
		chart?.destroy();
		chart = new uPlot(options(el.clientWidth || 600), current, el);
		const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => chart?.setSize({ width: el.clientWidth, height }));
		ro?.observe(el);
		return () => {
			ro?.disconnect();
			chart?.destroy();
			chart = null;
		};
	});
</script>

<figure class="space-y-1">
	<figcaption class="text-xs text-fg-muted">{label}</figcaption>
	{#if data[0].length === 0}
		<p class="rounded-md bg-surface-2 p-3 text-xs text-fg-faint">Нет данных за период.</p>
	{:else}
		<div bind:this={box} class="w-full" role="img" aria-label="График: {label}, точек {data[0].length}"></div>
	{/if}
</figure>
