/** «Сейчас» и строка пояснения «Плана бота». */
import type { Outlook, PublicState, WakeKind } from '$lib/api/types';
import { fmtMoment, fmtNum, fmtTime, mskDay } from '$lib/util/format';
import { activityLabel, PERSONAL_TASK } from '$lib/util/game';
import { val } from '$lib/util/observed';
import { actDetail, deedTag, deedText, readyText, scenarioText, timerLine, WAKE } from './text';

export interface NowView {
	/** Почему решение сейчас не исполняется или что идёт до него: пауза, неготовность, идущий
	 * сценарий, ручная очередь — все, что есть, по порядку. */
	blockers: string[];
	/** Решение планировщика; при `blockers` — условное: «когда пауза снимется — …»; пусто —
	 * решение и есть идущий сценарий. */
	decision: string;
	/** Момент следующего шага ожидания, ISO. */
	at: string | null;
	/** Цикл спит, а решение — действие: «Тогда: …» — что он сделает, проснувшись; иначе пусто. */
	then: string;
	/** Занятость по взгляду планировщика. */
	phase: string;
	/** «Держит 🔥: …» — запасы от дел; пусто — запаса нет. */
	reserves: string;
}

interface Blocker {
	text: string;
	when: string;
}

function blockers(plan: Outlook): Blocker[] {
	const loop = plan.loop;
	const out: Blocker[] = [];
	if (loop.paused) out.push({ text: '⏸ Планировщик на паузе', when: 'когда пауза снимется' });
	else if (loop.ready !== null) {
		out.push({ text: `⛔ Решения не исполняются: ${readyText(loop.ready)}`, when: 'когда это пройдёт' });
	}
	// На паузе ручной сценарий может идти (manual_while_paused): его видно и тогда.
	if (loop.current !== null) out.push({ text: `▶ Идёт сценарий: ${scenarioText(loop.current)}`, when: 'после него' });
	if (loop.manual_queue > 0) out.push({ text: `🖐 Ручных запусков в очереди: ${loop.manual_queue}`, when: 'после них' });
	if (!loop.auto) {
		out.push({ text: 'Планировщик выключен: бот исполняет только ручные запуски', when: 'если бы он работал' });
	}
	return out;
}

/** Зачем выбрано дело: основное, задание, запасное. */
function deedReason(reason: string): string {
	if (reason.startsWith('focus ')) return 'основное дело';
	if (reason.startsWith('personal ')) return 'под личное задание';
	const team = /^team \S+ (\d+\/\d+)/.exec(reason);
	if (team) return `под командное задание ${team[1]}`;
	if (reason.startsWith('best score')) return 'лучшее по оценке';
	return '';
}

const NO_TIMERS = '⏳ ждёт событий: таймеров нет';

function decisionText(plan: Outlook): { text: string; at: string | null } {
	const d = plan.decision;
	if (d.kind === 'act' && d.scenario !== null) {
		const detail = d.scenario.startsWith('deed:') ? deedReason(d.reason) : actDetail(d.scenario, d.params, plan);
		return { text: `${scenarioText(d.scenario)}${detail ? ` (${detail})` : ''}`, at: null };
	}
	if (d.reason === 'no_timers') return { text: NO_TIMERS, at: null };
	const timer = plan.wakeups.find((t) => !t.after_wake && t.at === d.until) ?? plan.wakeups[0];
	if (plan.phase === 'asleep' && timer?.kind === 'busy') return { text: '🛌 ждёт пробуждения', at: d.until };
	// timerLine несёт ключ (чей это кулдаун, какой источник обновить) — голый текст WAKE[kind] его теряет.
	const what = timer ? timerLine(timer, plan).text.toLowerCase() : d.reason;
	return { text: `⏳ ждёт: ${what}`, at: d.until };
}

interface LoopWait {
	/** `kind` или `kind:key` таймера, как в журнале, либо `no_timers`. */
	reason: string;
	/** Когда цикл проснётся сам, ISO. */
	wake: string;
}

/** Цикл спит до своего таймера, а план на текущий момент — действие: оно будет, когда цикл
 * проснётся (раньше — только если придёт сообщение игры). Срок — по более позднему из моментов
 * плана и часов экрана: план может быть из кеша сервера, а часы тикают раз в 30 с. */
function loopWait(plan: Outlook, now: Date): LoopWait | null {
	const { wait_reason: reason, wake_at: wake } = plan.loop;
	if (plan.decision.kind !== 'act' || reason === null || wake === null) return null;
	return Date.parse(wake) > Math.max(Date.parse(plan.now), now.getTime()) ? { reason, wake } : null;
}

function loopWaitText(plan: Outlook, { reason, wake }: LoopWait): string {
	if (reason === 'no_timers') return NO_TIMERS;
	const until = plan.loop.next_wake;
	const [kind = reason, ...rest] = reason.split(':');
	const key = rest.length > 0 ? rest.join(':') : null;
	const what =
		kind in WAKE
			? timerLine({ at: until ?? wake, kind: kind as WakeKind, key, after_wake: false }, plan).text.toLowerCase()
			: reason;
	// Проснётся раньше срока (предел простоя цикла) — срок самого ожидания рядом.
	const later = until !== null && Date.parse(until) !== Date.parse(wake) ? ` в ${fmtTime(until)}` : '';
	return `⏳ ждёт: ${what}${later}`;
}

/** «по последним данным (профиль — 19:21)»: занятость устарела, и остальное план считает на
 * последних известных значениях — время старейшего из них; пусто — всё на текущих данных. */
export function basisText(plan: Outlook): string {
	const basis = plan.basis;
	return basis ? `по последним данным (профиль — ${fmtMoment(basis.since, new Date(plan.now))})` : '';
}

/** Устаревшая занятость — сейчас свободен: дело, которое тогда шло, уже кончилось (дела начинает
 * только бот), или тогда был свободен. */
function staleBusyText(basis: NonNullable<Outlook['basis']>, now: Date): string {
	const ended = basis.ended;
	if (ended) return `Занятость устарела: ${activityLabel(ended.activity)} до ${fmtMoment(ended.until, now)} — уже свободен`;
	return `Занятость устарела: по данным на ${fmtMoment(basis.busy_at, now)} — свободен`;
}

function phaseText(plan: Outlook): string {
	const busy = plan.busy;
	if (plan.basis) return staleBusyText(plan.basis, new Date(plan.now));
	if (plan.phase === 'unknown') return 'Занятость неизвестна или устарела';
	if (busy === null) return 'Свободен';
	const what = activityLabel(busy.activity);
	return `${plan.phase === 'asleep' ? 'Спит' : 'Занят'}: ${what} до ${fmtTime(busy.until)}`;
}

/** Запасы 🔥 от дел: сколько и под что — бой Горбушки или вход в метро, к какому моменту. */
function reservesText(plan: Outlook): string {
	if (plan.reserves.length === 0) return '';
	const now = Date.parse(plan.now);
	const parts = plan.reserves.map((r) => {
		const due = Date.parse(r.at) <= now;
		if (r.kind === 'metro') return `${r.motivation} под метро (${due ? 'уже доступно' : `откроется в ${fmtTime(r.at)}`})`;
		return `${r.motivation} под бой Горбушки ${due ? 'сейчас' : `в ${fmtTime(r.at)}`}`;
	});
	return `Держит 🔥: ${parts.join(', ')}`;
}

/** План снят до итога идущего запуска, и его решение — этот же запуск: тот же сценарий, и его
 * параметры — те же у запуска (у запуска бывают ещё зафиксированные реестром). «После него» он не
 * повторится, а следующее решение будет по его итогу. Параметры запуска неизвестны — не он. */
function runsNow(plan: Outlook): boolean {
	const d = plan.decision;
	const { current, current_params: running } = plan.loop;
	if (d.kind !== 'act' || d.scenario === null || d.scenario !== current || running === null) return false;
	return Object.entries(d.params).every(([k, v]) => JSON.stringify(running[k]) === JSON.stringify(v));
}

/** `now` — часы экрана: наступил ли срок, до которого спит цикл. */
export function nowView(plan: Outlook, now: Date): NowView {
	const blocks = blockers(plan);
	const decision = decisionText(plan);
	const first = blocks[0];
	const common = { blockers: blocks.map((b) => b.text), phase: phaseText(plan), reserves: reservesText(plan) };
	const wait = first ? null : loopWait(plan, now);
	if (wait) return { ...common, decision: loopWaitText(plan, wait), at: wait.wake, then: `Тогда: ${decision.text}` };
	if (runsNow(plan)) return { ...common, decision: '', at: null, then: '' };
	return {
		...common,
		decision: first ? `${first.when} — ${decision.text}` : decision.text,
		at: decision.at,
		then: ''
	};
}

/** Зачем шаг дел возьмёт следующее дело. */
const WHY: Record<NonNullable<Outlook['hints']['next_deed']>['why'], string> = {
	personal: ', для личного задания',
	team: ', для командного задания',
	focus: '',
	best: ', лучшее по оценке',
	artifact: ', для сбора артефакта'
};

function focusText(plan: Outlook): string {
	const focus = plan.focus;
	const next = plan.hints.next_deed;
	// Во время сбора основные дела не идут: вся 🔥 — в дела тактики артефакта.
	if (next?.why === 'artifact') return `Сбор артефакта: вся 🔥 — в его дела. Следующее дело — ${deedText(next.deed)}.`;
	let base: string;
	if (focus.length === 0) base = 'Основных дел нет — дело выбирается по оценке.';
	else if (focus.length === 1) base = `Основное дело: ${deedText(focus[0]!.deed)} (сегодня ${focus[0]!.today}).`;
	else {
		const names = focus.map((f) => deedText(f.deed));
		const counts = focus.map((f) => `${deedTag(f.deed)} ${f.today}`).join(', ');
		base = `Основные дела: ${names.slice(0, -1).join(', ')} и ${names.at(-1)} по очереди (сегодня ${counts}).`;
	}
	// Следующее — то, что шаг дел выбрал бы среди доступных сейчас (с бэкенда: задания дня, потом
	// основные по очереди, потом лучшее по оценке); доступных нет — только очередь основных по
	// счётчикам: меньше запусков сегодня, при равенстве — раньше в списке.
	if (next) {
		// «Основные сейчас недоступны» повторило бы «Основных дел нет» строкой выше — только когда
		// основные дела вообще есть (просто сейчас ни одно не проходит).
		const why = next.why === 'best' && focus.length > 0 ? `${WHY.best}: основные сейчас недоступны` : WHY[next.why];
		return `${base} Следующее дело — ${deedText(next.deed)}${why}.`;
	}
	if (focus.length === 0) return base;
	const byCount = focus.reduce((best, f) => (f.today < best.today ? f : best));
	return `${base} Доступных дел сейчас нет; по счётчикам следующее основное — ${deedText(byCount.deed)}.`;
}

function personalText(state: PublicState, day: string): string {
	const task = val(state, 'daily_personal');
	if (!task || task.day !== day) return 'Личное задание: нет данных за сегодня';
	if (task.status === 'done') return 'Личное задание дня уже выполнено';
	if (task.status === 'active' && task.chosen) {
		const kind = PERSONAL_TASK[task.chosen.type ?? ''] ?? task.chosen.type ?? '';
		return `Личное задание: ${kind} ${fmtNum(task.current)}/${fmtNum(task.chosen.goal)} — его дела идут первыми`;
	}
	return 'Личное задание ещё не выбрано';
}

function teamText(state: PublicState, day: string): string {
	const team = val(state, 'team_task');
	if (!team || (team.day && team.day !== day)) return 'командное — нет данных за сегодня';
	if (team.status === 'none') return 'командное глава ещё не выбрал';
	if (team.status === 'done') return 'командное выполнено';
	return `командное: ${fmtNum(team.current)}/${fmtNum(team.goal)}${team.resource ? ` ${team.resource}` : ''}`;
}

/** Строка пояснения: основные дела и задания дня. */
export function explain(plan: Outlook, state: PublicState, now: Date): string {
	const day = mskDay(now);
	return `${focusText(plan)} ${personalText(state, day)}, ${teamText(state, day)}.`;
}
