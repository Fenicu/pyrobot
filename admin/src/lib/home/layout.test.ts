import { describe, expect, it } from 'vitest';
import { BLOCK_IDS } from './blocks';
import { DEFAULT_LAYOUT, DEFAULT_SIZE, normalizeLayout, phoneOrder, type HomeLayout } from './layout';

const clone = (l: HomeLayout): HomeLayout => JSON.parse(JSON.stringify(l));
const item = (l: HomeLayout, id: string) => l.items.find((i) => i.id === id);

describe('раскладка главной по умолчанию', () => {
	it('вариант A: слева Сейчас над Дальше, в центре Персонаж над Гаджетами, справа три блока', () => {
		expect(DEFAULT_LAYOUT).toEqual({
			version: 1,
			items: [
				{ id: 'now', x: 0, y: 0, w: 5, h: 7 },
				{ id: 'next', x: 0, y: 7, w: 5, h: 8 },
				{ id: 'character', x: 5, y: 0, w: 4, h: 7 },
				{ id: 'gadgets', x: 5, y: 7, w: 4, h: 8 },
				{ id: 'today', x: 9, y: 0, w: 3, h: 5 },
				{ id: 'daily', x: 9, y: 5, w: 3, h: 6 },
				{ id: 'artifact', x: 9, y: 11, w: 3, h: 3 }
			],
			hidden: []
		});
	});

	it('размер по умолчанию — у каждого блока, как в раскладке по умолчанию', () => {
		for (const id of BLOCK_IDS) {
			const { w, h } = item(DEFAULT_LAYOUT, id)!;
			expect(DEFAULT_SIZE[id]).toEqual({ w, h });
		}
	});
});

describe('normalizeLayout', () => {
	it('правильная раскладка — без изменений', () => {
		const l = clone(DEFAULT_LAYOUT);
		l.items[0] = { id: 'now', x: 1, y: 2, w: 4, h: 3 };
		l.hidden = ['artifact'];
		l.items = l.items.filter((i) => i.id !== 'artifact');
		expect(normalizeLayout(clone(l))).toEqual(l);
		expect(normalizeLayout(clone(DEFAULT_LAYOUT))).toEqual(DEFAULT_LAYOUT);
	});

	it('мусор и чужая версия — раскладка по умолчанию', () => {
		for (const raw of [null, undefined, 'x', 42, [], {}, { version: 2, items: [], hidden: [] }, { version: 1 }]) {
			expect(normalizeLayout(raw)).toEqual(DEFAULT_LAYOUT);
		}
	});

	it('пустая раскладка без скрытых — раскладка по умолчанию', () => {
		expect(normalizeLayout({ version: 1, items: [], hidden: [] })).toEqual(DEFAULT_LAYOUT);
	});

	it('неизвестный id отбрасывается', () => {
		const l = clone(DEFAULT_LAYOUT);
		const raw = { ...l, items: [...l.items, { id: 'weather', x: 0, y: 20, w: 3, h: 3 }] };
		expect(normalizeLayout(raw)).toEqual(DEFAULT_LAYOUT);
	});

	it('повторный id — берётся первый', () => {
		const l = clone(DEFAULT_LAYOUT);
		const raw = { ...l, items: [...l.items, { id: 'now', x: 7, y: 30, w: 2, h: 2 }] };
		const out = normalizeLayout(raw);
		expect(out.items.filter((i) => i.id === 'now')).toEqual([{ id: 'now', x: 0, y: 0, w: 5, h: 7 }]);
		expect(out.items).toHaveLength(7);
	});

	it('выход за 12 колонок прижимается: x 10 при w 4 → x 8', () => {
		const l = clone(DEFAULT_LAYOUT);
		l.items[4] = { id: 'today', x: 10, y: 0, w: 4, h: 5 };
		expect(item(normalizeLayout(l), 'today')).toEqual({ id: 'today', x: 8, y: 0, w: 4, h: 5 });
	});

	it('геометрия в допустимых пределах: w 1..12, x ≥ 0, y ≥ 0, h ≥ 1, целые', () => {
		const l = clone(DEFAULT_LAYOUT);
		l.items[0] = { id: 'now', x: -3, y: -1, w: 20, h: 0 };
		l.items[1] = { id: 'next', x: 2.6, y: 7.2, w: 0, h: 4.4 };
		const out = normalizeLayout(l);
		expect(item(out, 'now')).toEqual({ id: 'now', x: 0, y: 0, w: 12, h: 1 });
		expect(item(out, 'next')).toEqual({ id: 'next', x: 3, y: 7, w: 1, h: 4 });
	});

	it('недостающий блок добавляется ниже всех со своим размером по умолчанию', () => {
		const l = clone(DEFAULT_LAYOUT);
		l.items = l.items.filter((i) => i.id !== 'artifact');
		l.items[0] = { id: 'now', x: 0, y: 0, w: 5, h: 20 };
		const out = normalizeLayout(l);
		expect(out.items).toHaveLength(7);
		expect(item(out, 'artifact')).toEqual({ id: 'artifact', x: 0, y: 20, ...DEFAULT_SIZE.artifact });
	});

	it('несколько недостающих — друг под другом', () => {
		const out = normalizeLayout({ version: 1, items: [{ id: 'now', x: 0, y: 0, w: 5, h: 7 }], hidden: [] });
		expect(out.items.map((i) => i.id)).toEqual(BLOCK_IDS);
		let y = 7;
		for (const it of out.items.slice(1)) {
			expect(it).toEqual({ id: it.id, x: 0, y, ...DEFAULT_SIZE[it.id] });
			y += it.h;
		}
	});

	it('элемент с испорченной геометрией считается недостающим', () => {
		const l = clone(DEFAULT_LAYOUT);
		const raw = { ...l, items: l.items.map((i) => (i.id === 'daily' ? { id: 'daily', x: 'a', y: 1, w: 3 } : i)) };
		expect(item(normalizeLayout(raw), 'daily')).toEqual({ id: 'daily', x: 0, y: 15, ...DEFAULT_SIZE.daily });
	});

	it('скрытый блок не добавляется как недостающий; неизвестные и повторы в hidden отбрасываются', () => {
		const l = clone(DEFAULT_LAYOUT);
		l.items = l.items.filter((i) => i.id !== 'artifact');
		const out = normalizeLayout({ ...l, hidden: ['artifact', 'weather', 'artifact', 7] });
		expect(out.hidden).toEqual(['artifact']);
		expect(item(out, 'artifact')).toBeUndefined();
	});

	it('hidden не массив — пустой', () => {
		const out = normalizeLayout({ ...clone(DEFAULT_LAYOUT), hidden: 'now' });
		expect(out).toEqual(DEFAULT_LAYOUT);
	});

	it('результат — новый объект: правка не портит раскладку по умолчанию', () => {
		const out = normalizeLayout(null);
		out.items[0]!.x = 5;
		out.hidden.push('now');
		expect(item(DEFAULT_LAYOUT, 'now')!.x).toBe(0);
		expect(DEFAULT_LAYOUT.hidden).toEqual([]);
	});
});

describe('phoneOrder', () => {
	it('сверху вниз, в ряду — слева направо', () => {
		expect(phoneOrder(DEFAULT_LAYOUT)).toEqual(['now', 'character', 'today', 'daily', 'next', 'gadgets', 'artifact']);
	});

	it('скрытые исключены', () => {
		const l = clone(DEFAULT_LAYOUT);
		l.hidden = ['today', 'next'];
		expect(phoneOrder(l)).toEqual(['now', 'character', 'daily', 'gadgets', 'artifact']);
	});
});
