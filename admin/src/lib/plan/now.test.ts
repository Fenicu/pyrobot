import { describe, expect, it } from 'vitest';
import type { Outlook, StateOut } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import { explain, nowView } from './now';

const plan = fixture<Outlook>('outlook');
const prod = fixture<StateOut>('state');
const NOW = new Date(plan.now);

const withLoop = (loop: Partial<Outlook['loop']>): Outlook => ({ ...plan, loop: { ...plan.loop, ...loop } });

describe('«Сейчас»', () => {
	it('решение планировщика и занятость', () => {
		expect(nowView(plan)).toEqual({
			blockers: [],
			decision: '🤑 билеты лотереи (все — max)',
			at: null,
			phase: 'Занят: работа до 19:50'
		});
	});

	it('пауза, неготовность, идущий сценарий, очередь — первыми, решение условное', () => {
		expect(nowView(withLoop({ paused: true, ready: 'paused' }))).toMatchObject({
			blockers: ['⏸ Планировщик на паузе'],
			decision: 'когда пауза снимется — 🤑 билеты лотереи (все — max)'
		});
		expect(nowView(withLoop({ ready: 'tg_offline' })).blockers).toEqual(['⛔ Решения не исполняются: Telegram не в сети']);
		expect(nowView(withLoop({ current: 'deed:job' })).decision).toBe('после него — 🤑 билеты лотереи (все — max)');
		expect(nowView(withLoop({ manual_queue: 2 })).blockers).toEqual(['🖐 Ручных запусков в очереди: 2']);
	});

	it('на паузе идущий ручной сценарий и очередь видны вместе с паузой', () => {
		const view = nowView(withLoop({ paused: true, ready: 'paused', current: 'book', manual_queue: 1 }));
		expect(view.blockers).toEqual([
			'⏸ Планировщик на паузе',
			'▶ Идёт сценарий: 📒 книга',
			'🖐 Ручных запусков в очереди: 1'
		]);
		expect(view.decision).toBe('когда пауза снимется — 🤑 билеты лотереи (все — max)');
	});

	it('ожидание: причина и время следующего шага; во сне — пробуждение', () => {
		const until = plan.wakeups[0]!.at;
		const waiting: Outlook = { ...plan, decision: { kind: 'wait', scenario: null, params: {}, reason: 'busy', until } };
		expect(nowView(waiting)).toMatchObject({ decision: '⏳ ждёт: освободится', at: until });
		const asleep: Outlook = { ...waiting, phase: 'asleep', busy: { activity: 'sleep_bridge', until } };
		expect(nowView(asleep)).toMatchObject({ decision: '🛌 ждёт пробуждения', phase: 'Спит: сон под мостом до 19:50' });
		const idle: Outlook = { ...waiting, decision: { ...waiting.decision, reason: 'no_timers', until: null } };
		expect(nowView(idle).decision).toBe('⏳ ждёт событий: таймеров нет');
	});

	it('дело — с причиной выбора', () => {
		const deed: Outlook = {
			...plan,
			decision: { kind: 'act', scenario: 'deed:dconv', params: {}, reason: 'focus dconv (2 today)', until: null }
		};
		expect(nowView(deed).decision).toBe('⚙️→🔩 переработка (основное дело)');
		const team = { ...deed, decision: { ...deed.decision, reason: 'team dconv 60/120' } };
		expect(nowView(team).decision).toBe('⚙️→🔩 переработка (под командное задание 60/120)');
	});
});

describe('строка пояснения', () => {
	it('основные дела со счётчиками и задания дня со снимка', () => {
		expect(explain(plan, prod.state, NOW)).toBe(
			'Основные дела: добыча и переработка по очереди (сегодня ⛏ 3, ⚙️→🔩 2 — следующей будет переработка). ' +
				'Личное задание дня уже выполнено, командное выполнено.'
		);
	});

	it('следующее — доступное с бэкенда, даже если по счётчикам очередь другого', () => {
		// Добыча 0, переработка 1, но на добычу нет 💵: следующей будет переработка, как и решение.
		const poor: Outlook = {
			...plan,
			focus: [
				{ deed: 'deed:harvest', today: 0 },
				{ deed: 'deed:dconv', today: 1 }
			],
			hints: { ...plan.hints, next_focus: 'deed:dconv' }
		};
		expect(explain(poor, {}, NOW)).toContain('(сегодня ⛏ 0, ⚙️→🔩 1 — следующей будет переработка)');
	});

	it('доступного нет — порядок по счётчикам: меньше запусков, при равенстве — первое в списке', () => {
		const none: Outlook = { ...plan, hints: { ...plan.hints, next_focus: null } };
		expect(explain(none, {}, NOW)).toContain('по счётчикам следующей будет переработка');
		const even: Outlook = { ...none, focus: plan.focus.map((f) => ({ ...f, today: 2 })) };
		expect(explain(even, {}, NOW)).toContain('по счётчикам следующей будет добыча');
		expect(explain({ ...plan, focus: [] }, {}, NOW)).toMatch(/^Основных дел нет/);
		expect(explain(plan, {}, NOW)).toContain('Личное задание: нет данных за сегодня, командное — нет данных за сегодня.');
	});
});
