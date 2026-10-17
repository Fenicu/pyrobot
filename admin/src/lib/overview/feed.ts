import type { FeedItem, FeedType, FeedWatermark } from '$lib/api/types';
import { amountsText, KIND, kindText } from '$lib/daily/text';

/** Типы ленты по порядку показа. */
export const FEED_TYPES: { type: FeedType; label: string }[] = [
	{ type: 'levelup', label: 'Уровень' },
	{ type: 'battle', label: 'Битва' },
	{ type: 'factory', label: 'Фабрика' },
	{ type: 'metro', label: 'Метро' },
	{ type: 'lottery', label: 'Лотерея' },
	{ type: 'alert', label: 'Предупреждения' },
	{ type: 'manual', label: 'Вмешательства' }
];

export interface FeedFilters {
	/** Пусто - все аккаунты. */
	accountIds: number[];
	/** Пусто - все типы. */
	types: FeedType[];
}

export const NO_FILTERS: FeedFilters = { accountIds: [], types: [] };

/** Порядок на сервере: время события, затем ранг (уведомления раньше журнала), затем id. */
function compare(a: FeedItem, b: FeedItem): number {
	const time = Date.parse(b.at) - Date.parse(a.at);
	if (time !== 0) return time;
	const rank = (i: FeedItem) => (i.key.startsWith('n:') ? 1 : 0);
	if (rank(a) !== rank(b)) return rank(b) - rank(a);
	return Number(b.key.slice(2)) - Number(a.key.slice(2));
}

/** Слить пришедшее в список: повторы по ключу убираются, порядок - по времени события, поэтому
 * поздняя запись со старым временем встаёт на своё место, а загруженный хвост остаётся. */
export function mergeFeed(current: FeedItem[], incoming: FeedItem[]): FeedItem[] {
	const byKey = new Map<string, FeedItem>();
	for (const i of current) byKey.set(i.key, i);
	for (const i of incoming) byKey.set(i.key, i);
	return [...byKey.values()].sort(compare);
}

/** Знак приёма в параметр `after`: «<журнал>:<уведомления>». */
export function formatAfter(w: FeedWatermark): string {
	return `${w.ledger}:${w.notifications}`;
}

export function parseWatermark(raw: string): FeedWatermark | null {
	const m = /^(\d+):(\d+)$/.exec(raw);
	return m ? { ledger: Number(m[1]), notifications: Number(m[2]) } : null;
}

/** Ключ набора фильтров: одинаковый выбор в любом порядке - один ключ; «все» - пустой выбор или
 * выбор всех типов. */
export function filtersKey(f: FeedFilters): string {
	const ids = [...new Set(f.accountIds)].sort((a, b) => a - b);
	const types = [...new Set(f.types)];
	const allTypes = types.length === 0 || FEED_TYPES.every((t) => types.includes(t.type));
	return `a:${ids.length ? ids.join(',') : '*'}|t:${allTypes ? '*' : FEED_TYPES.filter((t) => types.includes(t.type)).map((t) => t.type).join(',')}`;
}

/** Значок события: вид журнала прихода; для уведомлений - по уровню и типу. */
export function feedIcon(i: FeedItem): string {
	if (i.key.startsWith('l:')) return KIND[i.kind]?.icon ?? '•';
	if (i.type === 'manual') return '✋';
	if (i.level === 'error') return '⛔';
	if (i.level === 'warn') return '⚠️';
	return '•';
}

/** Текст события: журнал собирается из вида и сумм (незнакомый вид - как есть), уведомление - его
 * текст (нет текста - код). */
export function feedText(i: FeedItem): string {
	if (i.key.startsWith('n:')) return i.text ?? i.kind;
	const sums = amountsText(i.amounts ?? {});
	const items = Object.entries(i.items ?? {})
		.map(([name, n]) => `${name} ×${n}`)
		.join(', ');
	return [kindText(i.kind), sums, items].filter(Boolean).join(' · ');
}
