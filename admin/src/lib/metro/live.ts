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

/** Кадров нет дольше — связь с забегом потеряна (движок упал, поток или GET недоступны). */
export const LOST_AFTER_MS = 5 * 60_000;
/** Выброс прошёл дольше — забег точно кончился, а кадра конца нет. */
export const KICK_GRACE_MS = 2 * 60_000;

/** Момент конца забега по кадру `running: false`: по доле бюджета (`started_at + used × total_s`) —
 * точно, пока доля не упёрлась в 1; иначе — момент получения, но не позже выброса (`kick_at`) и не
 * позже `RUN_MAX_MS` от начала (кадр взят GET-ом позже конца). */
export function endedAt(frame: MetroLive, receivedAt: number): number {
	const started = Date.parse(frame.started_at);
	const { total_s: total, used } = frame.budget;
	if (total && used < 1) return started + used * total * 1000;
	const kick = frame.kick_at ? Date.parse(frame.kick_at) : Infinity;
	return Math.min(receivedAt, kick, started + RUN_MAX_MS);
}

/** С какого момента идущий кадр — потерянная связь: `LOST_AFTER_MS` без кадров или `KICK_GRACE_MS`
 * после выброса; null — кадр конца. */
export function lostAt(frame: MetroLive, receivedAt: number): number | null {
	if (!frame.running) return null;
	const kick = frame.kick_at ? Date.parse(frame.kick_at) + KICK_GRACE_MS : Infinity;
	return Math.min(receivedAt + LOST_AFTER_MS, kick);
}

/** Карточка на главной: забег идёт (или связь с ним потеряна меньше `OUTCOME_KEEP_MS` назад) либо
 * кончился меньше `OUTCOME_KEEP_MS` назад. */
export function shown(frame: MetroLive | null, receivedAt: number | null, now: number): boolean {
	if (frame === null) return false;
	const got = receivedAt ?? now;
	const since = frame.running ? lostAt(frame, got)! : endedAt(frame, got);
	return now - since <= OUTCOME_KEEP_MS;
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
