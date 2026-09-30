<script lang="ts">
	import {
		chartOption,
		readPalette,
		toChart,
		zoomPatch,
		zoomRange,
		type ChartParams,
		type ZoomEvent
	} from '$lib/metrics/chart';
	import { init, type ECharts } from '$lib/metrics/echarts';
	import type { Marker } from '$lib/metrics/events';
	import { followed, link, shareZoom } from '$lib/metrics/sync';

	interface Props {
		/** Подпись над графиком: «💵 деньги · 47». */
		label: string;
		/** Имя в подсказке: «💵 деньги». */
		name: string;
		data: [number[], number[]];
		/** Окно графика, секунды Unix: общее у всех графиков. */
		range: [number, number];
		height?: number;
		/** События окна — пунктирные вертикали со значком наверху (слив в акции, сон…). */
		markers?: Marker[];
	}
	let { label, name, data, range, height = 180, markers = [] }: Props = $props();
	let box = $state<HTMLDivElement>();
	let chart = $state.raw<ECharts | null>(null);
	// Перекраска при смене темы (класс .light на <html>).
	let theme = $state(0);
	// Ширина — только для прореживания значков меток: при изменении размера график не пересобирается,
	// чтобы не сбросить выделенный участок.
	let width = 0;

	$effect(() => {
		const el = box;
		if (!el) return;
		const c = init(el);
		// Курсор с подсказкой и выделенный участок — на всех графиках страницы сразу.
		const unlink = link(c);
		width = el.clientWidth;
		chart = c;
		const resize =
			typeof ResizeObserver === 'undefined'
				? null
				: new ResizeObserver(() => {
						width = el.clientWidth;
						c.resize();
					});
		resize?.observe(el);
		const recolor = new MutationObserver(() => theme++);
		recolor.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
		return () => {
			resize?.disconnect();
			recolor.disconnect();
			unlink();
			c.dispose();
			chart = null;
		};
	});

	$effect(() => {
		const c = chart;
		if (!c) return;
		void theme;
		const p: ChartParams = {
			name,
			data,
			markers,
			range,
			width,
			colors: readPalette(),
			view: { range },
			followed: () => followed(c)
		};
		c.setOption(chartOption(p), { notMerge: true });
		c.off('datazoom');
		// Каждый график пересчитывает свою ось значений; ведущий передаёт участок остальным. Не внутри
		// обработки выделения: setOption посреди неё смешивает старую и новую разметку оси.
		c.on('datazoom', (event) => {
			p.view.range = zoomRange(event as ZoomEvent, range);
			shareZoom(c, toChart(p.view.range[0]), toChart(p.view.range[1]));
			setTimeout(() => {
				if (!c.isDisposed()) c.setOption(zoomPatch(p, p.view.range));
			});
		});
		// Мышью участок выделяется сразу, как раньше; на телефоне — после касания значка «выделить
		// участок», иначе протяжка по графику не прокручивала бы страницу.
		if (typeof matchMedia === 'function' && matchMedia('(pointer: fine)').matches) {
			c.dispatchAction({ type: 'takeGlobalCursor', key: 'dataZoomSelect', dataZoomSelectActive: true });
		}
	});
</script>

<figure class="space-y-1">
	<figcaption class="text-xs text-fg-muted">{label}</figcaption>
	{#if data[0].length === 0}
		<p class="rounded-md bg-surface-2 p-3 text-xs text-fg-faint">Нет данных за период.</p>
	{:else}
		<div
			bind:this={box}
			class="w-full"
			style:height="{height}px"
			role="img"
			aria-label="График: {label}, точек {data[0].length}{markers.length ? `, меток ${markers.length}` : ''}"
		></div>
	{/if}
</figure>
