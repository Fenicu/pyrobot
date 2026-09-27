/** Форматирование для экранов: время — по Москве (игровые сутки и события — по МСК). */
export const TZ = 'Europe/Moscow';

const time = new Intl.DateTimeFormat('ru-RU', { timeZone: TZ, hour: '2-digit', minute: '2-digit' });
const timeSec = new Intl.DateTimeFormat('ru-RU', {
	timeZone: TZ,
	hour: '2-digit',
	minute: '2-digit',
	second: '2-digit'
});
const dayMonth = new Intl.DateTimeFormat('ru-RU', { timeZone: TZ, day: '2-digit', month: '2-digit' });
const dayKey = new Intl.DateTimeFormat('en-CA', {
	timeZone: TZ,
	year: 'numeric',
	month: '2-digit',
	day: '2-digit'
});
const num = new Intl.NumberFormat('ru-RU');

export function toDate(value: string | Date | null | undefined): Date | null {
	if (value === null || value === undefined) return null;
	const d = value instanceof Date ? value : new Date(value);
	return Number.isNaN(d.getTime()) ? null : d;
}

/** 22:05 */
export function fmtTime(value: string | Date | null | undefined, seconds = false): string {
	const d = toDate(value);
	return d ? (seconds ? timeSec : time).format(d) : '—';
}

/** 27.09 22:05; сегодняшнее (МСК) — только время. */
export function fmtMoment(value: string | Date | null | undefined, now = new Date(), seconds = false): string {
	const d = toDate(value);
	if (!d) return '—';
	const t = fmtTime(d, seconds);
	return mskDay(d) === mskDay(now) ? t : `${dayMonth.format(d)} ${t}`;
}

/** Календарный день по МСК: 2026-09-27. */
export function mskDay(value: Date): string {
	return dayKey.format(value);
}

/** Начало суток МСК, в которые попадает момент. */
export function mskDayStart(value: Date): Date {
	return new Date(`${mskDay(value)}T00:00:00+03:00`);
}

/** «через 38 мин», «5 мин назад», «через 2 ч 10 мин». */
export function fmtRelative(value: string | Date | null | undefined, now = new Date()): string {
	const d = toDate(value);
	if (!d) return '—';
	const diff = d.getTime() - now.getTime();
	const span = fmtSpan(Math.abs(diff) / 1000);
	if (Math.abs(diff) < 30_000) return 'сейчас';
	return diff > 0 ? `через ${span}` : `${span} назад`;
}

/** Длительность: 45 с, 9 мин, 2 ч 10 мин, 1 д 3 ч. */
export function fmtSpan(totalSeconds: number): string {
	const s = Math.round(totalSeconds);
	if (s < 60) return `${s} с`;
	const m = Math.round(s / 60);
	if (m < 60) return `${m} мин`;
	const h = Math.floor(m / 60);
	if (h < 24) return m % 60 ? `${h} ч ${m % 60} мин` : `${h} ч`;
	const days = Math.floor(h / 24);
	return h % 24 ? `${days} д ${h % 24} ч` : `${days} д`;
}

/** 21 946 (неразрывные пробелы в разрядах). */
export function fmtNum(value: number | null | undefined): string {
	return value === null || value === undefined ? '—' : num.format(value);
}

/** 17.52M, 18.2K — для больших чисел в узких местах. */
export function fmtCompact(value: number | null | undefined): string {
	if (value === null || value === undefined) return '—';
	const abs = Math.abs(value);
	if (abs >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
	if (abs >= 1e4) return `${(value / 1e3).toFixed(1)}K`;
	return fmtNum(value);
}
