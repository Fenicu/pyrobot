<script lang="ts">
	import EyeOff from '@lucide/svelte/icons/eye-off';
	import GripHorizontal from '@lucide/svelte/icons/grip-horizontal';
	import { GridStack, type GridItemHTMLElement, type GridStackWidget } from 'gridstack';
	import 'gridstack/dist/gridstack.min.css';
	import { onMount, untrack, type Snippet } from 'svelte';
	import { BLOCK_IDS, BLOCK_TITLES, type BlockId } from './blocks';
	import { COLUMNS, MAX_BLOCK_H, MAX_ROWS, sameLayout, type HomeLayout } from './layout';

	interface Props {
		layout: HomeLayout;
		editing: boolean;
		/** Содержимое блоков: обёртки сетки объявлены здесь статически, блоки рисует страница. */
		blocks: Record<BlockId, Snippet>;
		/** Раскладка на экране разошлась с `layout`: перетаскивание или поправка сетки при расстановке. */
		onchange?: (l: HomeLayout) => void;
		onhide?: (id: BlockId) => void;
	}
	let { layout, editing, blocks, onchange, onhide }: Props = $props();

	let root: HTMLDivElement;
	let grid = $state.raw<GridStack | null>(null);
	// Расстановка идёт сама: события сетки в это время — не действия пользователя.
	let applying = false;
	// Последняя расставленная раскладка: та же самая — повторно не расставляется (сетка могла
	// её поправить, и страница уже знает итог).
	let applied: HomeLayout | null = null;

	const wrapper = (id: BlockId) => root.querySelector<GridItemHTMLElement>(`:scope > [data-block="${id}"]`);

	// Геометрия видимых блоков — из сетки, без HTML; скрытые — те, кого в сетке нет.
	function read(g: GridStack): HomeLayout {
		const items = (g.save(false) as GridStackWidget[]).map((n) => ({
			id: n.id as BlockId,
			x: n.x ?? 0,
			y: n.y ?? 0,
			w: n.w ?? 1,
			h: n.h ?? 1
		}));
		return { version: 1, items, hidden: BLOCK_IDS.filter((id) => !items.some((i) => i.id === id)) };
	}

	// Расставить раскладку заново: все блоки снимаются с сетки (узлы остаются) и ставятся сверху
	// вниз — так переезды не толкают друг друга. Если сетка что-то поправила — сообщить странице.
	function apply(g: GridStack, l: HomeLayout) {
		if (l === applied) return;
		applied = l;
		// Класс скрытого — всегда по раскладке: сетка без блоков читается как «все скрыты» и
		// расстановку пропустит, а обёртки вне сетки иначе видны.
		for (const id of BLOCK_IDS) wrapper(id)?.classList.toggle('hidden', l.hidden.includes(id));
		if (sameLayout(read(g), l)) return;
		applying = true;
		try {
			g.batchUpdate();
			for (const el of g.getGridItems()) g.removeWidget(el, false, false);
			const visible = l.items.filter((i) => !l.hidden.includes(i.id)).sort((a, b) => a.y - b.y || a.x - b.x);
			for (const { id, x, y, w, h } of visible) {
				const el = wrapper(id);
				if (el) g.makeWidget(el, { id, x, y, w, h, maxH: MAX_BLOCK_H });
			}
			g.batchUpdate(false);
		} finally {
			applying = false;
		}
		const got = read(g);
		if (!sameLayout(got, l)) onchange?.(got);
	}

	onMount(() => {
		// Без columnOpts gridstack сам число колонок не меняет.
		const g = GridStack.init(
			{
				column: COLUMNS,
				maxRow: MAX_ROWS,
				cellHeight: 44,
				margin: 7,
				staticGrid: true,
				auto: false,
				handle: '.block-grip',
				resizable: { handles: 'se' }
			},
			root
		);
		if (!g) return;
		apply(g, untrack(() => layout));
		g.on('change', () => {
			if (!applying) onchange?.(read(g));
		});
		grid = g;
		return () => {
			grid = null;
			g.destroy(false);
		};
	});

	$effect(() => {
		const g = grid;
		const l = layout;
		if (g) untrack(() => apply(g, l));
	});

	$effect(() => {
		grid?.setStatic(!editing);
	});
</script>

{#snippet tools(id: BlockId)}
	{#if editing}
		<!-- Полоса над строкой заголовка — ручка перетаскивания (блок тянется «за заголовок»). -->
		<div class="block-grip" title="Перетащить">
			<span class="block-tools">
				<GripHorizontal class="size-4 text-fg-muted" aria-hidden="true" />
				<button
					type="button"
					class="block-hide"
					aria-label="Скрыть «{BLOCK_TITLES[id]}»"
					title="Скрыть"
					onclick={() => onhide?.(id)}
				>
					<EyeOff class="size-4" aria-hidden="true" />
				</button>
			</span>
		</div>
	{/if}
{/snippet}

<!-- Обёртки — прямые дети сетки без {#if}/{#each}: gridstack переставляет их узлы при перетаскивании. -->
<div bind:this={root} class="home-grid grid-stack">
	<div class="grid-stack-item" data-block="now"><div class="grid-stack-item-content">{@render blocks.now()}{@render tools('now')}</div></div>
	<div class="grid-stack-item" data-block="next"><div class="grid-stack-item-content">{@render blocks.next()}{@render tools('next')}</div></div>
	<div class="grid-stack-item" data-block="character">
		<div class="grid-stack-item-content">{@render blocks.character()}{@render tools('character')}</div>
	</div>
	<div class="grid-stack-item" data-block="gadgets">
		<div class="grid-stack-item-content">{@render blocks.gadgets()}{@render tools('gadgets')}</div>
	</div>
	<div class="grid-stack-item" data-block="today"><div class="grid-stack-item-content">{@render blocks.today()}{@render tools('today')}</div></div>
	<div class="grid-stack-item" data-block="daily"><div class="grid-stack-item-content">{@render blocks.daily()}{@render tools('daily')}</div></div>
	<div class="grid-stack-item" data-block="artifact">
		<div class="grid-stack-item-content">{@render blocks.artifact()}{@render tools('artifact')}</div>
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
	.home-grid:not(:global(.grid-stack-static)) :global(.grid-stack-item-content > .card) {
		border-style: dashed;
		border-color: var(--color-accent);
	}
	.block-grip {
		position: absolute;
		inset: 0 0 auto 0;
		display: flex;
		height: 2.75rem;
		align-items: center;
		justify-content: flex-end;
		padding: 0 0.5rem;
		cursor: move;
	}
	.block-tools {
		display: flex;
		align-items: center;
		gap: 0.25rem;
		border: 1px solid var(--color-line);
		border-radius: 999px;
		background: var(--color-surface-2);
		padding: 0.125rem 0.25rem 0.125rem 0.5rem;
	}
	.block-hide {
		display: inline-flex;
		border-radius: 999px;
		padding: 0.25rem;
		color: var(--color-fg-muted);
		cursor: pointer;
	}
	.block-hide:hover {
		color: var(--color-fg);
		background: var(--color-surface);
	}
</style>
