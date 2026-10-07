/** Живой кадр забега метро (`metro_live`, `GET /metro/live`): какой кадр новее, сколько держать
 * итог на главной и тексты карточки. */
import type { MetroLive } from '$lib/api/types';
import { LEAVE_REASON, mapOf, OUTCOME_TEXT, type MapModel } from './model';

/** Кадры замерли дольше — карточка пишет, сколько назад обновлено (ступени зависшего шага). */
export const STALE_AFTER_MS = 30_000;
/** Итог прошедшего забега виден столько после конца. */
export const OUTCOME_KEEP_MS = 30 * 60_000;
/** Дольше забег не идёт: граница конца, когда момент выброса неизвестен. */
export const RUN_MAX_MS = 90 * 60_000;
/** Событий, накопленных за забег, не больше. */
export const EVENTS_KEPT = 2_000;

/** `next` новее `cur`: другой забег — по `message_id` (сообщение нового забега позже), тот же — по
 * шагам, при равных — конец забега важнее идущего кадра. Поздний ответ GET после `reset` так не
 * затирает кадр потока. */
export function supersedes(next: MetroLive, cur: MetroLive | null): boolean {
	if (cur === null) return true;
	if (next.message_id !== cur.message_id) return next.message_id > cur.message_id;
	if (next.steps !== cur.steps) return next.steps > cur.steps;
	return !next.running || cur.running;
}

const eventKey = (e: unknown) => JSON.stringify(e);
const stepOf = (e: unknown) => (e as { step?: number }).step ?? 0;

/** Кадр несёт последние 30 событий: события того же забега из прежних кадров дописываются
 * спереди, чтобы значки ранних находок не пропадали с карты. */
export function withHistory(next: MetroLive, cur: MetroLive | null): MetroLive {
	if (cur === null || cur.message_id !== next.message_id || cur.events.length === 0) return next;
	const fresh = new Set(next.events.map(eventKey));
	const from = next.events.length ? stepOf(next.events[0]) : Infinity;
	const older = cur.events.filter((e) => stepOf(e) <= from && !fresh.has(eventKey(e)));
	return { ...next, events: [...older, ...next.events].slice(-EVENTS_KEPT) };
}

/** Момент конца забега по кадру `running: false`: получен потоком — тогда; взят GET-ом позже —
 * не позже выброса (`kick_at`) и не позже `RUN_MAX_MS` от начала. */
export function endedAt(frame: MetroLive, receivedAt: number): number {
	const kick = frame.kick_at ? Date.parse(frame.kick_at) : Infinity;
	return Math.min(receivedAt, kick, Date.parse(frame.started_at) + RUN_MAX_MS);
}

/** Карточка на главной: забег идёт или кончился меньше `OUTCOME_KEEP_MS` назад. */
export function shown(frame: MetroLive | null, receivedAt: number | null, now: number): boolean {
	if (frame === null) return false;
	return frame.running || now - endedAt(frame, receivedAt ?? now) <= OUTCOME_KEEP_MS;
}

export const MODE_TEXT: Record<string, string> = {
	explore: 'обход',
	frontier: 'ищу выход',
	leave: 'к выходу'
};

/** «обход», «к выходу: не успеть до битвы». */
export function modeText(frame: MetroLive): string {
	const mode = MODE_TEXT[frame.mode] ?? frame.mode;
	if (frame.mode !== 'leave' || !frame.leave_reason) return mode;
	return `${mode}: ${LEAVE_REASON[frame.leave_reason] ?? frame.leave_reason}`;
}

/** Причины остановки забега (`ScenarioResult.reason` сценария метро). */
const STOP_TEXT: Record<string, string> = {
	paused: 'пауза',
	interrupted: 'сбой сценария',
	lost: 'потерял позицию на карте',
	unexpected_wall: 'стена на месте прохода',
	exit_not_found: 'выход не найден',
	no_route_to_exit: 'нет пути к выходу',
	unexpected_screen: 'незнакомый экран',
	screen_changed: 'экран сменили вручную',
	stuck_unknown: 'завис ход',
	moving: 'ход не закончился',
	timeout: 'игра не ответила'
};

/** Исход по последнему кадру — теми же словами, что список забегов. */
export function outcomeText(frame: MetroLive): string {
	if (frame.outcome === 'finished') return frame.mode === 'leave' ? OUTCOME_TEXT.self : OUTCOME_TEXT.ejected;
	const reason = frame.outcome ?? '';
	return `${OUTCOME_TEXT.stopped}: ${STOP_TEXT[reason.split(':')[0]!] ?? reason}`;
}

/** Модель карты: кадр по форме — как запись забега, выход — из кадра. */
export function liveMap(frame: MetroLive): MapModel {
	return mapOf({ ...frame, summary: { exit: frame.exit } });
}
