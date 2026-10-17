import type { OverviewTotals, TotalsDelta } from '$lib/api/types';
import { moneySigned, signed } from '$lib/daily/text';

export type Period = 1 | 7 | 30;
export type MetricKey = 'money' | 'exp' | 'knowledge' | 'details' | 'raw' | 'glory';

export const PERIODS: { days: Period; label: string }[] = [
	{ days: 1, label: 'Сегодня' },
	{ days: 7, label: '7 дней' },
	{ days: 30, label: '30 дней' }
];
export const DEFAULT_PERIOD: Period = 7;

/** Показатели итогов и графика по порядку показа. */
export const METRICS: { key: MetricKey; icon: string; title: string }[] = [
	{ key: 'money', icon: '💵', title: 'деньги' },
	{ key: 'exp', icon: '💡', title: 'опыт' },
	{ key: 'knowledge', icon: '📚', title: 'знания' },
	{ key: 'details', icon: '⚙️', title: 'детали' },
	{ key: 'raw', icon: '🔩', title: 'сырьё' },
	{ key: 'glory', icon: '🏆', title: 'личная слава' }
];

export const TOTALS_POLL_TODAY_MS = 30_000;
export const TOTALS_POLL_LONG_MS = 300_000;
const DAY_MS = 86_400_000;

/** Период из `?days=`: только 1, 7, 30. */
export function parseDays(raw: string | null): Period {
	const n = Number(raw);
	return n === 1 || n === 7 || n === 30 ? n : DEFAULT_PERIOD;
}

/** «Сегодня» меняется быстро, длинные периоды - медленно (плюс обновление на возвращение во вкладку). */
export function pollIntervalMs(days: Period): number {
	return days === 1 ? TOTALS_POLL_TODAY_MS : TOTALS_POLL_LONG_MS;
}

/** Изменение за период: «+$91 200», «—» без данных (ложного нуля нет); если в сумму вошли не все
 * `total` аккаунтов - «… · по 5 из 7». */
export function formatDelta(d: TotalsDelta, key: MetricKey, total: number): string {
	if (d.delta === null) return '—';
	const value = key === 'money' ? moneySigned(d.delta) : signed(d.delta);
	return d.accounts < total ? `${value} · по ${d.accounts} из ${total}` : value;
}

/** Повышения уровня: null - окно длиннее хранения метрик. */
export function levelsText(levels: number | null): string {
	if (levels === null) return '—';
	return levels > 0 ? `+${levels}` : String(levels);
}

function daysText(n: number): string {
	const last = n % 10;
	const word = n % 100 >= 11 && n % 100 <= 14 ? 'дней' : last === 1 ? 'день' : last >= 2 && last <= 4 ? 'дня' : 'дней';
	return `${n} ${word}`;
}

/** Окно начинается раньше порога хранения метрик: баланс и уровни посчитать нельзя. */
export function windowNote(data: OverviewTotals): string | null {
	const start = Date.parse(`${data.first}T00:00:00+03:00`);
	const floor = Date.parse(data.metrics_floor);
	if (start >= floor) return null;
	const kept = Math.round((Date.parse(data.now) - floor) / DAY_MS);
	return `Метрики хранятся ${daysText(kept)}: изменение за этот период не посчитать.`;
}

export interface ChartData {
	/** Сутки по возрастанию. */
	days: string[];
	series: {
		name: string;
		/** Значение столбца по суткам; null - нет данных. */
		data: (number | null)[];
		/** Сутки неполные (сегодня, до запуска журнала): столбец штрихуется. */
		partial: boolean[];
	}[];
}

/** Столбцы по суткам: ряд на аккаунт, один показатель; столбцы суток - те же, что в «Итогах дня». */
export function chartSeries(data: OverviewTotals, metric: MetricKey, nameOf: (id: number) => string): ChartData {
	const days = [...new Set(data.accounts.flatMap((a) => a.days.map((d) => d.day)))].sort();
	return {
		days,
		series: data.accounts.map((a) => {
			const byDay = new Map(a.days.map((d) => [d.day, d]));
			return {
				name: nameOf(a.id),
				data: days.map((day) => byDay.get(day)?.balance[metric] ?? null),
				partial: days.map((day) => byDay.get(day)?.partial ?? true)
			};
		})
	};
}
