/** «Сейчас» и строка пояснения «Плана бота». */
import type { Outlook, PublicState } from '$lib/api/types';
import { fmtNum, fmtTime, mskDay } from '$lib/util/format';
import { activityLabel, PERSONAL_TASK } from '$lib/util/game';
import { val } from '$lib/util/observed';
import { actDetail, deedText, readyText, scenarioText, WAKE } from './text';

export interface NowView {
	/** Почему решение сейчас не исполняется или что идёт до него: пауза, неготовность, идущий
	 * сценарий, ручная очередь — все, что есть, по порядку. */
	blockers: string[];
	/** Решение планировщика; при `blockers` — условное: «когда пауза снимется — …». */
	decision: string;
	/** Момент следующего шага ожидания, ISO. */
	at: string | null;
	/** Занятость по взгляду планировщика. */
	phase: string;
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

function decisionText(plan: Outlook): { text: string; at: string | null } {
	const d = plan.decision;
	if (d.kind === 'act' && d.scenario !== null) {
		const detail = d.scenario.startsWith('deed:') ? deedReason(d.reason) : actDetail(d.scenario, d.params, plan);
		return { text: `${scenarioText(d.scenario)}${detail ? ` (${detail})` : ''}`, at: null };
	}
	if (d.reason === 'no_timers') return { text: '⏳ ждёт событий: таймеров нет', at: null };
	const timer = plan.wakeups.find((t) => !t.after_wake && t.at === d.until) ?? plan.wakeups[0];
	if (plan.phase === 'asleep' && timer?.kind === 'busy') return { text: '🛌 ждёт пробуждения', at: d.until };
	const what = timer ? WAKE[timer.kind].text.toLowerCase() : d.reason;
	return { text: `⏳ ждёт: ${what}`, at: d.until };
}

function phaseText(plan: Outlook): string {
	const busy = plan.busy;
	if (plan.phase === 'unknown') return 'Занятость неизвестна или устарела';
	if (busy === null) return 'Свободен';
	const what = activityLabel(busy.activity);
	return `${plan.phase === 'asleep' ? 'Спит' : 'Занят'}: ${what} до ${fmtTime(busy.until)}`;
}

export function nowView(plan: Outlook): NowView {
	const blocks = blockers(plan);
	const decision = decisionText(plan);
	const first = blocks[0];
	return {
		blockers: blocks.map((b) => b.text),
		decision: first ? `${first.when} — ${decision.text}` : decision.text,
		at: decision.at,
		phase: phaseText(plan)
	};
}

/** Зачем шаг дел возьмёт следующее дело. */
const WHY: Record<NonNullable<Outlook['hints']['next_deed']>['why'], string> = {
	personal: ', для личного задания',
	team: ', для командного задания',
	focus: '',
	best: ', лучшее по оценке: основные сейчас недоступны'
};

function focusText(plan: Outlook): string {
	const focus = plan.focus;
	let base: string;
	if (focus.length === 0) base = 'Основных дел нет — дело выбирается по оценке.';
	else if (focus.length === 1) base = `Основное дело: ${deedText(focus[0]!.deed)} (сегодня ${focus[0]!.today}).`;
	else {
		const names = focus.map((f) => deedText(f.deed));
		const counts = focus.map((f) => `${scenarioText(f.deed).split(' ')[0]} ${f.today}`).join(', ');
		base = `Основные дела: ${names.slice(0, -1).join(', ')} и ${names.at(-1)} по очереди (сегодня ${counts}).`;
	}
	// Следующее — то, что шаг дел выбрал бы среди доступных сейчас (с бэкенда: задания дня, потом
	// основные по очереди, потом лучшее по оценке); доступных нет — только очередь основных по
	// счётчикам: меньше запусков сегодня, при равенстве — раньше в списке.
	const next = plan.hints.next_deed;
	if (next) return `${base} Следующее дело — ${deedText(next.deed)}${WHY[next.why]}.`;
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
