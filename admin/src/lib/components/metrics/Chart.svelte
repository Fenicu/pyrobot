<script lang="ts">
	import uPlot from 'uplot';
	import 'uplot/dist/uPlot.min.css';
	import { AXIS_FONT, axisNumbers, axisSize, TIME_VALUES } from '$lib/metrics/axis';
	import type { Marker } from '$lib/metrics/events';
	import { TZ } from '$lib/util/format';

	interface Props {
		label: string;
		data: [number[], number[]];
		height?: number;
		/** События окна — пунктирные вертикали со значком наверху (слив в акции, сон…). */
		markers?: Marker[];
	}
	let { label, data, height = 140, markers = [] }: Props = $props();
	let box = $state<HTMLDivElement>();
	let chart: uPlot | null = null;

	function css(name: string, fallback: string): string {
		return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
	}

	/** Метки поверх графика: линия на каждое событие, значок — если не наезжает на соседний. */
	function drawMarkers(u: uPlot, marks: Marker[]) {
		if (marks.length === 0) return;
		const ratio = uPlot.pxRatio;
		const { left, top, width, height: h } = u.bbox;
		const ctx = u.ctx;
		ctx.save();
		// Состояние контекста после серий uPlot — с прозрачной заливкой: цвет значка задаётся явно.
		ctx.globalAlpha = 1;
		ctx.strokeStyle = css('--fg-faint', '#777');
		ctx.fillStyle = css('--fg-muted', '#999');
		ctx.lineWidth = ratio;
		ctx.setLineDash([3 * ratio, 3 * ratio]);
		ctx.font = `${12 * ratio}px system-ui, sans-serif`;
		ctx.textAlign = 'center';
		ctx.textBaseline = 'top';
		let lastIcon = Number.NEGATIVE_INFINITY;
		for (const m of marks) {
			const x = Math.round(u.valToPos(m.x, 'x', true));
			if (x < left || x > left + width) continue;
			ctx.beginPath();
			ctx.moveTo(x, top);
			ctx.lineTo(x, top + h);
			ctx.stroke();
			if (x - lastIcon >= 16 * ratio) {
				ctx.fillText(m.icon, x, top + ratio);
				lastIcon = x;
			}
		}
		ctx.restore();
	}

	function options(width: number): uPlot.Options {
		const axis = {
			stroke: css('--fg-muted', '#999'),
			font: AXIS_FONT,
			grid: { stroke: css('--line-soft', '#333'), width: 1 }
		};
		return {
			width,
			height,
			legend: { show: false },
			hooks: { draw: [(u: uPlot) => drawMarkers(u, markers)] },
			cursor: { drag: { x: true, y: false } },
			tzDate: (ts) => uPlot.tzDate(new Date(ts * 1e3), TZ),
			scales: { x: { time: true } },
			axes: [
				{ ...axis, values: TIME_VALUES },
				{
					...axis,
					values: (_u: uPlot, splits: number[]) => axisNumbers(splits),
					size: (_u: uPlot, values: string[] | null) => axisSize(values)
				}
			],
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
		<div bind:this={box} class="w-full" role="img" aria-label="График: {label}, точек {data[0].length}{markers.length ? `, меток ${markers.length}` : ''}"></div>
	{/if}
</figure>
