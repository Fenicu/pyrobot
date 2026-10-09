import { cleanup, render } from '@testing-library/svelte';
import type { GridItemHTMLElement } from 'gridstack';
import { createRawSnippet, flushSync } from 'svelte';
import { afterEach, describe, expect, it } from 'vitest';
import { BLOCK_IDS, BLOCK_TITLES, type BlockId } from './blocks';
import HomeGrid from './HomeGrid.svelte';
import { DEFAULT_LAYOUT, type HomeLayout } from './layout';

const blocks = Object.fromEntries(
	BLOCK_IDS.map((id) => [
		id,
		createRawSnippet(() => ({ render: () => `<section class="card"><p>${BLOCK_TITLES[id]}</p></section>` }))
	])
) as Record<BlockId, ReturnType<typeof createRawSnippet>>;

const wrapper = (c: HTMLElement, id: BlockId) => c.querySelector<HTMLElement>(`[data-block="${id}"]`)!;

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
});
