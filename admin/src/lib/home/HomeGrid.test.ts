import { cleanup, render, screen, within } from '@testing-library/svelte';
import type { GridItemHTMLElement } from 'gridstack';
import { createRawSnippet, flushSync } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { BLOCK_IDS, BLOCK_TITLES, type BlockId } from './blocks';
import HomeGrid from './HomeGrid.svelte';
import { DEFAULT_LAYOUT, sameLayout, type HomeLayout } from './layout';

const blocks = Object.fromEntries(
	BLOCK_IDS.map((id) => [
		id,
		createRawSnippet(() => ({ render: () => `<section class="card"><p>${BLOCK_TITLES[id]}</p></section>` }))
	])
) as Record<BlockId, ReturnType<typeof createRawSnippet>>;

const wrapper = (c: HTMLElement, id: BlockId) => c.querySelector<HTMLElement>(`[data-block="${id}"]`)!;
const clone = (l: HomeLayout): HomeLayout => JSON.parse(JSON.stringify(l));

// Геометрия сетки по атрибутам gs-* (gridstack не пишет x/y = 0).
function geometry(c: HTMLElement) {
	return BLOCK_IDS.filter((id) => (wrapper(c, id) as GridItemHTMLElement).gridstackNode).map((id) => {
		const el = wrapper(c, id);
		const n = (k: string) => Number(el.getAttribute(`gs-${k}`) ?? (k === 'w' || k === 'h' ? 1 : 0));
		return { id, x: n('x'), y: n('y'), w: n('w'), h: n('h') };
	});
}

function noOverlaps(items: { x: number; y: number; w: number; h: number }[]) {
	for (const a of items) {
		for (const b of items) {
			if (a === b) continue;
			const hit = a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
			expect(hit, `${JSON.stringify(a)} × ${JSON.stringify(b)}`).toBe(false);
		}
	}
}

afterEach(cleanup);

describe('сетка главной', () => {
	it('видимые блоки расставлены по раскладке, скрытый — с классом hidden и не в сетке', () => {
		const layout: HomeLayout = {
			version: 1,
			items: DEFAULT_LAYOUT.items.filter((i) => i.id !== 'artifact'),
			hidden: ['artifact']
		};
		const { container } = render(HomeGrid, { layout, editing: false, blocks });
		for (const { id, x, y, w, h } of layout.items) {
			const el = wrapper(container, id);
			expect([el.getAttribute('gs-x') ?? '0', el.getAttribute('gs-y') ?? '0']).toEqual([`${x}`, `${y}`]);
			expect([el.getAttribute('gs-w'), el.getAttribute('gs-h')]).toEqual([`${w}`, `${h}`]);
			expect(el).toHaveTextContent(BLOCK_TITLES[id]);
		}
		const hidden = wrapper(container, 'artifact');
		expect(hidden).toHaveClass('hidden');
		expect((hidden as GridItemHTMLElement).gridstackNode).toBeUndefined();
		expect((wrapper(container, 'now') as GridItemHTMLElement).gridstackNode?.id).toBe('now');
	});

	it('просмотр — сетка статична, правка — нет', async () => {
		const { container, rerender } = render(HomeGrid, { layout: DEFAULT_LAYOUT, editing: false, blocks });
		const grid = container.querySelector('.grid-stack')!;
		expect(grid).toHaveClass('grid-stack-static');
		await rerender({ editing: true });
		flushSync();
		expect(grid).not.toHaveClass('grid-stack-static');
		await rerender({ editing: false });
		flushSync();
		expect(grid).toHaveClass('grid-stack-static');
	});

	it('наложение при расстановке: сетка раздвигает блоки и сообщает итог странице, без пересечений', () => {
		const layout = clone(DEFAULT_LAYOUT);
		// «Персонаж» налезает на «Сейчас», «Гаджеты» — на «Персонаж».
		layout.items = layout.items.map((i) =>
			i.id === 'character' ? { ...i, x: 3, y: 2 } : i.id === 'gadgets' ? { ...i, x: 4, y: 4 } : i
		);
		const onchange = vi.fn();
		const { container } = render(HomeGrid, { layout, editing: false, blocks, onchange });
		const got = geometry(container);
		expect(got).toHaveLength(7);
		noOverlaps(got);
		expect(onchange).toHaveBeenCalledTimes(1);
		const reported = onchange.mock.calls[0]![0] as HomeLayout;
		expect(sameLayout(reported, { version: 1, items: got, hidden: [] })).toBe(true);
	});

	it('согласованная раскладка при расстановке — странице ничего не сообщается', () => {
		const onchange = vi.fn();
		render(HomeGrid, { layout: DEFAULT_LAYOUT, editing: false, blocks, onchange });
		expect(onchange).not.toHaveBeenCalled();
	});

	it('новая раскладка без пересоздания: блоки переставляются, скрытый уходит из сетки, возвращённый — в неё', async () => {
		const onchange = vi.fn();
		const { container, rerender } = render(HomeGrid, { layout: DEFAULT_LAYOUT, editing: false, blocks, onchange });
		const moved = clone(DEFAULT_LAYOUT);
		moved.items = moved.items
			.filter((i) => i.id !== 'artifact')
			.map((i) => (i.id === 'today' ? { ...i, x: 0, y: 0 } : i.id === 'now' ? { ...i, x: 3, w: 2 } : i));
		moved.items = moved.items.map((i) =>
			i.id === 'next' ? { ...i, x: 0, y: 8, w: 5 } : i.id === 'daily' ? { ...i, y: 0 } : i
		);
		moved.hidden = ['artifact'];
		// «Сегодня» 3×8 слева сверху, «Сейчас» 2×6 рядом, «Дальше» под ними; справа «Итоги дня» поднялись.
		await rerender({ layout: moved });
		flushSync();
		expect(sameLayout({ version: 1, items: geometry(container), hidden: ['artifact'] }, moved)).toBe(true);
		expect(wrapper(container, 'artifact')).toHaveClass('hidden');
		expect((wrapper(container, 'artifact') as GridItemHTMLElement).gridstackNode).toBeUndefined();

		await rerender({ layout: DEFAULT_LAYOUT });
		flushSync();
		expect(sameLayout({ version: 1, items: geometry(container), hidden: [] }, DEFAULT_LAYOUT)).toBe(true);
		expect(wrapper(container, 'artifact')).not.toHaveClass('hidden');
		expect((wrapper(container, 'artifact') as GridItemHTMLElement).gridstackNode?.id).toBe('artifact');
		expect(onchange).not.toHaveBeenCalled();
	});

	it('правка: у блока кнопка «скрыть» и ручка перетаскивания; в просмотре их нет', async () => {
		const onhide = vi.fn();
		const { container, rerender } = render(HomeGrid, { layout: DEFAULT_LAYOUT, editing: false, blocks, onhide });
		expect(screen.queryByRole('button', { name: /^Скрыть/ })).toBeNull();
		await rerender({ editing: true });
		flushSync();
		for (const id of BLOCK_IDS) {
			expect(within(wrapper(container, id)).getByRole('button', { name: `Скрыть «${BLOCK_TITLES[id]}»` })).toBeVisible();
			expect(wrapper(container, id).querySelector('.block-grip')).not.toBeNull();
		}
		screen.getByRole('button', { name: 'Скрыть «Гаджеты»' }).click();
		expect(onhide).toHaveBeenCalledWith('gadgets');
		await rerender({ editing: false });
		flushSync();
		expect(screen.queryByRole('button', { name: /^Скрыть/ })).toBeNull();
	});

	it('все блоки скрыты: на свежей сетке ни одна обёртка не видна', () => {
		const layout: HomeLayout = { version: 1, items: [], hidden: [...BLOCK_IDS] };
		const onchange = vi.fn();
		const { container } = render(HomeGrid, { layout, editing: false, blocks, onchange });
		for (const id of BLOCK_IDS) {
			expect(wrapper(container, id)).toHaveClass('hidden');
			expect((wrapper(container, id) as GridItemHTMLElement).gridstackNode).toBeUndefined();
		}
		expect(onchange).not.toHaveBeenCalled();
	});
});

