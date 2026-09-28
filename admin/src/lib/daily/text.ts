/** Тексты «Итогов дня»: виды журнала прихода, ресурсы, знаки и счётчики — коды как в движке. */
import type { DayOut, KindOut } from '$lib/api/types';
import { fmtNum, TZ } from '$lib/util/format';

export interface KindLabel {
	icon: string;
	label: string;
}

/** Виды журнала прихода (`app/engine/state/ledger.py`); сверку с движком держит
 * `tests/test_daily_kinds.py`. */
export const KIND: Record<string, KindLabel> = {
	deed: { icon: '⛏', label: 'дела' },
	deed_start: { icon: '⛏', label: 'начало дел' },
	hotel: { icon: '🛏', label: 'отель' },
	lottery_tickets: { icon: '🎟', label: 'билеты лотереи' },
	gorbushka_ticket: { icon: '🎟', label: 'билет Горбушки' },
	robbery: { icon: '🥷', label: 'ограбление' },
	book: { icon: '📒', label: 'книги' },
	card: { icon: '💳', label: 'карты' },
	prizebox: { icon: '🎁', label: 'коробка' },
	container: { icon: '🗳', label: 'контейнеры' },
	gorbushka_fight: { icon: '🏛', label: 'Горбушка' },
	lottery_win: { icon: '🤑', label: 'выигрыш лотереи' },
	lottery_skills: { icon: '🤑', label: 'навыки лотереи' },
	metro: { icon: '🚇', label: 'метро' },
	bulls: { icon: '🐂', label: 'биржевики' },
	task: { icon: '👫', label: 'задание' },
	factory: { icon: '🏭', label: 'фабрика' },
	battle: { icon: '⚔', label: 'битва' },
	dividends: { icon: '📈', label: 'дивиденды' },
	levelup: { icon: '🔨', label: 'новый уровень' },
	sleep: { icon: '🛌', label: 'сон' },
	robbery_fight: { icon: '🥊', label: 'драка с грабителем' },
	tangerine_gift: { icon: '🍊', label: 'подарок за 🍊' },
	exchange: { icon: '🔁', label: 'обмен символа' },
	shark: { icon: '🦈', label: 'акула' }
};

/** Изменение за день: ключи `DayOut.balance` по порядку показа. */
export const BALANCE: { key: string; icon: string; title: string }[] = [
	{ key: 'money', icon: '💵', title: 'деньги' },
	{ key: 'exp', icon: '💡', title: 'опыт' },
	{ key: 'knowledge', icon: '📚', title: 'знания' },
	{ key: 'details', icon: '⚙️', title: 'детали' },
	{ key: 'raw', icon: '🔩', title: 'сырьё' },
	{ key: 'glory', icon: '🏆', title: 'личная слава' }
];

/** Символы сумм журнала прихода (`AMOUNT_KEYS` движка); деньги — со знаком $. */
const AMOUNT: Record<string, string> = {
	exp: '💡',
	knowledge: '📚',
	details: '⚙️',
	raw: '🔩',
	upgrades_white: '⚪',
	upgrades_blue: '🔵',
	upgrades_red: '🔴',
	trophies: '🏆',
	containers_small: '🗳М',
	containers_medium: '🗳С'
};
const AMOUNT_ORDER = ['money', 'trophies', 'exp', 'knowledge', 'details', 'raw', 'upgrades_white', 'upgrades_blue', 'upgrades_red', 'containers_small', 'containers_medium'];

const weekday = new Intl.DateTimeFormat('ru-RU', { timeZone: TZ, weekday: 'short' });

/** Подпись вида; незнакомый — как есть. */
export function kindText(kind: string): string {
	const k = KIND[kind];
	return k ? `${k.icon} ${k.label}` : kind;
}

/** +4 812, −120, 0 (минус — U+2212). */
export function signed(n: number): string {
	if (n > 0) return `+${fmtNum(n)}`;
	if (n < 0) return `−${fmtNum(-n)}`;
	return '0';
}

/** +$1 240, −$340. */
export function moneySigned(n: number): string {
	if (n > 0) return `+$${fmtNum(n)}`;
	if (n < 0) return `−$${fmtNum(-n)}`;
	return '$0';
}

/** Суммы события: «+$403, +25 ⚙️, +18 🔩». */
export function amountsText(amounts: Record<string, number>): string {
	const keys = Object.keys(amounts).sort((a, b) => rank(a) - rank(b));
	return keys
		.filter((k) => amounts[k])
		.map((k) => (k === 'money' ? moneySigned(amounts[k]!) : `${signed(amounts[k]!)} ${AMOUNT[k] ?? k}`))
		.join(', ');
}

function rank(key: string): number {
	const i = AMOUNT_ORDER.indexOf(key);
	return i < 0 ? AMOUNT_ORDER.length : i;
}

function times(count: number): string {
	return count > 1 ? ` ×${count}` : '';
}

/** Разовое: «📒 книги ×4 · +720 💡». */
export function incomeText(k: KindOut): string {
	const sums = amountsText(k.amounts);
	return `${kindText(k.kind)}${times(k.count)}${sums ? ` · ${sums}` : ''}`;
}

/** Потери и траты: «🥷 ограбление −$340». */
export function lossText(k: KindOut): string {
	const sums = amountsText(k.amounts);
	return `${kindText(k.kind)}${times(k.count)}${sums ? ` ${sums}` : ''}`;
}

/** Предметов крафта за день (штук). */
export function itemsCount(day: DayOut): number {
	return Object.values(day.items).reduce((a, n) => a + n, 0);
}

/** Событий разового за день. */
export function incomeCount(day: DayOut): number {
	return day.income.reduce((a, k) => a + k.count, 0);
}

/** 💵 потерь и трат за день (положительное число). */
export function lossesMoney(day: DayOut): number {
	return -day.losses.reduce((a, k) => a + (k.amounts.money ?? 0), 0);
}

/** «27.09» из «2026-09-27». */
export function dayShort(day: string): string {
	const [, month, date] = day.split('-');
	return `${date}.${month}`;
}

/** «вс» — день недели по Москве. */
export function weekdayShort(day: string): string {
	return weekday.format(new Date(`${day}T12:00:00+03:00`));
}

/** «Сегодня, 28.09» или «27.09 вс». */
export function dayLabel(day: string, today: string): string {
	return day === today ? `Сегодня, ${dayShort(day)}` : `${dayShort(day)} ${weekdayShort(day)}`;
}

export interface Average {
	balance: Record<string, number | null>;
	items: number | null;
	income: number | null;
	losses: number | null;
}

function mean(values: number[]): number | null {
	return values.length ? Math.round(values.reduce((a, b) => a + b, 0) / values.length) : null;
}

/** Среднее за 7 дней до сегодня: по полным дням (не `partial`), у изменения баланса — без
 * «нет данных». `days` — ответ `/daily`, сегодня первым. */
export function average(days: DayOut[]): Average {
	const full = days.slice(1, 8).filter((d) => !d.partial);
	const balance: Record<string, number | null> = {};
	for (const { key } of BALANCE) {
		balance[key] = mean(full.flatMap((d) => (d.balance[key]?.delta ?? null) === null ? [] : [d.balance[key]!.delta!]));
	}
	return {
		balance,
		items: mean(full.map(itemsCount)),
		income: mean(full.map(incomeCount)),
		losses: mean(full.map(lossesMoney))
	};
}
