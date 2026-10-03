import type { DayOut } from '$lib/api/types';
import { fmtCompact, fmtDayMonth } from '$lib/util/format';

const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;
/** Темп опыта — среднее по стольким последним полным суткам. */
const WINDOW = 7;
const MIN_DAYS = 2;

export interface LevelForecast {
	perDay: number;
	etaMs: number;
	at: Date;
}

/** Когда наберётся опыт до следующего уровня при среднем темпе по полным суткам «Итогов дня»:
 * неполные (сегодняшние) сутки и сутки без покрытия журналом не берутся. */
export function levelForecast(days: DayOut[], expLeft: number | null, now: Date): LevelForecast | null {
	if (expLeft === null || expLeft <= 0) return null;
	const deltas = [...days]
		.sort((a, b) => b.day.localeCompare(a.day))
		.flatMap((d) => {
			const exp = d.balance.exp;
			return !d.partial && exp?.covered === true && exp.delta !== null ? [exp.delta] : [];
		})
		.slice(0, WINDOW);
	if (deltas.length < MIN_DAYS) return null;
	const perDay = deltas.reduce((a, b) => a + b, 0) / deltas.length;
	if (perDay <= 0) return null;
	const etaMs = (expLeft / perDay) * DAY_MS;
	return { perDay, etaMs, at: new Date(now.getTime() + etaMs) };
}

/** «≈ 33 дн. (к 05.11) при 16.3K/сут»; меньше суток — «≈ 5 ч». */
export function levelForecastText(f: LevelForecast): string {
	const span =
		f.etaMs >= DAY_MS ? `${Math.max(1, Math.round(f.etaMs / DAY_MS))} дн.` : `${Math.max(1, Math.round(f.etaMs / HOUR_MS))} ч`;
	return `≈ ${span} (к ${fmtDayMonth(f.at)}) при ${fmtCompact(f.perDay)}/сут`;
}
