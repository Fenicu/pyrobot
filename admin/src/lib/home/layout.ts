import { BLOCK_IDS, type BlockId } from './blocks';

/** Место блока в сетке главной: 12 колонок, высота — в рядах сетки. */
export interface LayoutItem {
	id: BlockId;
	x: number;
	y: number;
	w: number;
	h: number;
}

/** Сохранённая раскладка: геометрия видимых блоков и список скрытых. */
export interface HomeLayout {
	version: 1;
	items: LayoutItem[];
	hidden: BlockId[];
}

export const COLUMNS = 12;

export const DEFAULT_LAYOUT: HomeLayout = {
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
};

export const DEFAULT_SIZE = Object.fromEntries(DEFAULT_LAYOUT.items.map(({ id, w, h }) => [id, { w, h }])) as Record<
	BlockId,
	{ w: number; h: number }
>;

const isBlockId = (v: unknown): v is BlockId => typeof v === 'string' && (BLOCK_IDS as readonly string[]).includes(v);
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const copy = (l: HomeLayout): HomeLayout => ({ version: 1, items: l.items.map((i) => ({ ...i })), hidden: [...l.hidden] });

function readItem(raw: unknown): LayoutItem | null {
	if (typeof raw !== 'object' || raw === null) return null;
	const { id, x, y, w, h } = raw as Record<string, unknown>;
	if (!isBlockId(id) || !isNum(x) || !isNum(y) || !isNum(w) || !isNum(h)) return null;
	const width = clamp(Math.round(w), 1, COLUMNS);
	return {
		id,
		x: clamp(Math.round(x), 0, COLUMNS - width),
		y: Math.max(0, Math.round(y)),
		w: width,
		h: Math.max(1, Math.round(h))
	};
}

/** Раскладка от сервера (или любой другой версии клиента) — к виду, который можно расставить. */
export function normalizeLayout(raw: unknown): HomeLayout {
	if (typeof raw !== 'object' || raw === null) return copy(DEFAULT_LAYOUT);
	const { version, items: rawItems, hidden: rawHidden } = raw as Record<string, unknown>;
	if (version !== 1 || !Array.isArray(rawItems)) return copy(DEFAULT_LAYOUT);

	const items: LayoutItem[] = [];
	for (const r of rawItems) {
		const it = readItem(r);
		if (it && !items.some((i) => i.id === it.id)) items.push(it);
	}
	const hidden = [...new Set(Array.isArray(rawHidden) ? rawHidden.filter(isBlockId) : [])];
	if (items.length === 0 && hidden.length === 0) return copy(DEFAULT_LAYOUT);

	let bottom = Math.max(0, ...items.map((i) => i.y + i.h));
	for (const id of BLOCK_IDS) {
		if (items.some((i) => i.id === id) || hidden.includes(id)) continue;
		items.push({ id, x: 0, y: bottom, ...DEFAULT_SIZE[id] });
		bottom += DEFAULT_SIZE[id].h;
	}
	return { version: 1, items, hidden };
}

/** Порядок одной колонки (телефон, узкий экран): сверху вниз, в ряду — слева направо; без скрытых. */
export function phoneOrder(l: HomeLayout): BlockId[] {
	return l.items
		.filter((i) => !l.hidden.includes(i.id))
		.sort((a, b) => a.y - b.y || a.x - b.x)
		.map((i) => i.id);
}
