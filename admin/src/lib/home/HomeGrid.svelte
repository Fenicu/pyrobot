<script lang="ts">
	import { GridStack, type GridStackWidget } from 'gridstack';
	import 'gridstack/dist/gridstack.min.css';
	import { onMount, type Snippet } from 'svelte';
	import type { BlockId } from './blocks';
	import { COLUMNS, type HomeLayout } from './layout';

	interface Props {
		layout: HomeLayout;
		editing: boolean;
		/** Содержимое блоков: обёртки сетки объявлены здесь статически, блоки рисует страница. */
		blocks: Record<BlockId, Snippet>;
		onchange?: (l: HomeLayout) => void;
	}
	let { layout, editing, blocks, onchange }: Props = $props();

	let root: HTMLDivElement;
	let grid = $state.raw<GridStack | null>(null);

	// Геометрия видимых блоков — из сетки, без HTML; скрытые — как были.
	function read(g: GridStack): HomeLayout {
		const items = (g.save(false) as GridStackWidget[]).map((n) => ({
			id: n.id as BlockId,
			x: n.x ?? 0,
			y: n.y ?? 0,
			w: n.w ?? 1,
			h: n.h ?? 1
		}));
		return { version: 1, items, hidden: [...layout.hidden] };
	}

	onMount(() => {
		// Раскладка расставляется один раз при создании; без columnOpts gridstack сам число колонок не меняет.
		const g = GridStack.init(
			{ column: COLUMNS, cellHeight: 44, margin: 7, staticGrid: true, auto: false, handle: '.card-title' },
			root
		);
		if (!g) return;
		const wrapper = (id: BlockId) => root.querySelector<HTMLElement>(`:scope > [data-block="${id}"]`);
		g.batchUpdate();
		for (const { id, x, y, w, h } of layout.items) {
			const el = wrapper(id);
			if (el && !layout.hidden.includes(id)) g.makeWidget(el, { id, x, y, w, h });
		}
		g.batchUpdate(false);
		for (const id of layout.hidden) wrapper(id)?.classList.add('hidden');
		g.on('change', () => onchange?.(read(g)));
		grid = g;
		return () => {
			grid = null;
			g.destroy(false);
		};
	});

	$effect(() => {
		grid?.setStatic(!editing);
	});
</script>

<!-- Обёртки — прямые дети сетки без {#if}/{#each}: gridstack переставляет их узлы при перетаскивании. -->
<div bind:this={root} class="home-grid grid-stack">
	<div class="grid-stack-item" data-block="now"><div class="grid-stack-item-content">{@render blocks.now()}</div></div>
	<div class="grid-stack-item" data-block="next"><div class="grid-stack-item-content">{@render blocks.next()}</div></div>
	<div class="grid-stack-item" data-block="character">
		<div class="grid-stack-item-content">{@render blocks.character()}</div>
	</div>
	<div class="grid-stack-item" data-block="gadgets">
		<div class="grid-stack-item-content">{@render blocks.gadgets()}</div>
	</div>
	<div class="grid-stack-item" data-block="today"><div class="grid-stack-item-content">{@render blocks.today()}</div></div>
	<div class="grid-stack-item" data-block="daily"><div class="grid-stack-item-content">{@render blocks.daily()}</div></div>
	<div class="grid-stack-item" data-block="artifact">
		<div class="grid-stack-item-content">{@render blocks.artifact()}</div>
	</div>
</div>

<style>
	/* Блок занимает свою клетку целиком; длинное содержимое прокручивается внутри, заголовок на месте. */
	.home-grid :global(.grid-stack-item-content) {
		overflow: hidden;
	}
	.home-grid :global(.grid-stack-item-content > .card) {
		display: flex;
		height: 100%;
		flex-direction: column;
	}
	.home-grid :global(.grid-stack-item-content > .card > :last-child) {
		min-height: 0;
		flex: 1 1 auto;
		overflow: auto;
	}
	.home-grid:not(:global(.grid-stack-static)) :global(.card-title) {
		cursor: move;
	}
</style>
