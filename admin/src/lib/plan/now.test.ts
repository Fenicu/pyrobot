import { describe, expect, it } from 'vitest';
import type { Outlook, StateOut } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import { basisText, explain, nowView } from './now';

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
			then: '',
			phase: 'Занят: работа до 19:50',
			reserves: 'Держит 🔥: 2 под метро (откроется в 20:21)'
		});
	});

	it('запас 🔥: под бой Горбушки и метро по времени, уже доступное — «сейчас»; без запаса строки нет', () => {
		const both: Outlook = {
			...plan,
			reserves: [
				{ kind: 'gorbushka', motivation: 1, at: '2026-09-27T16:38:00Z' },
				{ kind: 'metro', motivation: 2, at: '2026-09-27T17:21:00Z' }
			]
		};
		expect(nowView(both).reserves).toBe('Держит 🔥: 1 под бой Горбушки в 19:38, 2 под метро (откроется в 20:21)');
		const due: Outlook = {
			...plan,
			reserves: [
				{ kind: 'metro', motivation: 2, at: plan.now },
				{ kind: 'gorbushka', motivation: 3, at: plan.now }
			]
		};
		expect(nowView(due).reserves).toBe('Держит 🔥: 2 под метро (уже доступно), 3 под бой Горбушки сейчас');
		expect(nowView({ ...plan, reserves: [] }).reserves).toBe('');
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

	it('ожидание отсрочки — с тем, чья она (не только «кончится отсрочка»)', () => {
		const until = '2026-09-27T17:10:00Z';
		const wakeups: Outlook['wakeups'] = [{ at: until, kind: 'cooldown', key: 'deed:job', after_wake: false }];
		const waiting: Outlook = {
			...plan,
			wakeups,
			decision: { kind: 'wait', scenario: null, params: {}, reason: 'cooldown:deed:job', until }
		};
		expect(nowView(waiting).decision).toBe('⏳ ждёт: кончится отсрочка: работа');
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

describe('«Сейчас», пока цикл спит', () => {
	const BOOK = '2026-09-27T16:42:54Z';
	const sleeping = withLoop({ next_wake: BOOK, wait_reason: 'book_ready', wake_at: BOOK });

	it('решение — действие, а цикл ждёт таймера: сначала ожидание, действие — «тогда»', () => {
		expect(nowView(sleeping)).toMatchObject({
			blockers: [],
			decision: '⏳ ждёт: прочитать книгу',
			at: BOOK,
			then: 'Тогда: 🤑 билеты лотереи (все — max)'
		});
		const cooldown = withLoop({ next_wake: BOOK, wait_reason: 'cooldown:refresh:profile', wake_at: BOOK });
		expect(nowView(cooldown).decision).toBe('⏳ ждёт: кончится отсрочка: обновить профиль');
	});

	it('ожидание дольше предела простоя: проснётся раньше срока — время следующего шага по нему', () => {
		const early = '2026-09-27T16:44:00Z';
		const view = nowView(withLoop({ next_wake: '2026-09-27T19:05:03Z', wait_reason: 'sleep_window', wake_at: early }));
		expect(view).toMatchObject({ decision: '⏳ ждёт: сон 7 ч под мостом в 22:05', at: early });
		expect(view.then).toBe('Тогда: 🤑 билеты лотереи (все — max)');
		const idle = nowView(withLoop({ next_wake: null, wait_reason: 'no_timers', wake_at: early }));
		expect(idle).toMatchObject({ decision: '⏳ ждёт событий: таймеров нет', at: early });
	});

	it('срок ожидания наступил, цикл не ждёт или исполняет — действие как есть', () => {
		const due = withLoop({ next_wake: plan.now, wait_reason: 'book_ready', wake_at: plan.now });
		expect(nowView(due)).toMatchObject({ decision: '🤑 билеты лотереи (все — max)', at: null, then: '' });
		expect(nowView(plan).then).toBe('');
		const running = withLoop({ current: 'book', next_wake: BOOK, wait_reason: 'book_ready', wake_at: BOOK });
		expect(nowView(running)).toMatchObject({
			blockers: ['▶ Идёт сценарий: 📒 книга'],
			decision: 'после него — 🤑 билеты лотереи (все — max)',
			then: ''
		});
	});

	it('пауза и неготовность — первыми, как раньше', () => {
		const paused = withLoop({ paused: true, ready: 'paused', next_wake: BOOK, wait_reason: 'book_ready', wake_at: BOOK });
		expect(nowView(paused)).toMatchObject({ decision: 'когда пауза снимется — 🤑 билеты лотереи (все — max)', then: '' });
	});

	it('решение — ожидание: как раньше, без «тогда»', () => {
		const waiting: Outlook = {
			...sleeping,
			decision: { kind: 'wait', scenario: null, params: {}, reason: 'busy', until: plan.wakeups[0]!.at }
		};
		expect(nowView(waiting)).toMatchObject({ decision: '⏳ ждёт: освободится', then: '' });
	});
});

describe('занятость устарела', () => {
	const stale = fixture<Outlook>('outlook_stale');

	it('по данным на момент её наблюдения — свободен; неизвестная — как раньше', () => {
		expect(nowView(stale).phase).toBe('Занятость устарела: по данным на 19:24 — свободен');
		expect(basisText(stale)).toBe('по данным на 19:24');
		const unknown: Outlook = { ...stale, basis_at: null, basis_considered: [] };
		expect(nowView(unknown).phase).toBe('Занятость неизвестна или устарела');
		expect(basisText(unknown)).toBe('');
	});

	it('наблюдение не сегодня — с датой', () => {
		const old: Outlook = { ...stale, basis_at: '2026-09-26T20:10:00Z' };
		expect(basisText(old)).toBe('по данным на 26.09 23:10');
	});
});

describe('строка пояснения', () => {
	it('основные дела со счётчиками, следующее дело и задания дня со снимка', () => {
		expect(explain(plan, prod.state, NOW)).toBe(
			'Основные дела: добыча и переработка по очереди (сегодня ⛏ 3, ⚙️→🔩 2). Следующее дело — переработка. ' +
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
			hints: { ...plan.hints, next_deed: { deed: 'deed:dconv', why: 'focus' } }
		};
		expect(explain(poor, {}, NOW)).toContain('(сегодня ⛏ 0, ⚙️→🔩 1). Следующее дело — переработка.');
	});

	it('дело под задание — с причиной', () => {
		const team: Outlook = { ...plan, hints: { ...plan.hints, next_deed: { deed: 'deed:walk', why: 'team' } } };
		expect(explain(team, {}, NOW)).toContain('Следующее дело — прогулка, для командного задания.');
		const personal: Outlook = { ...plan, hints: { ...plan.hints, next_deed: { deed: 'deed:job', why: 'personal' } } };
		expect(explain(personal, {}, NOW)).toContain('Следующее дело — работа, для личного задания.');
		const best: Outlook = { ...plan, hints: { ...plan.hints, next_deed: { deed: 'deed:job', why: 'best' } } };
		expect(explain(best, {}, NOW)).toContain('Следующее дело — работа, лучшее по оценке: основные сейчас недоступны.');
	});

	it('основных дел нет — «лучшее по оценке» без повтора «основные недоступны»', () => {
		const noFocus: Outlook = {
			...plan,
			focus: [],
			hints: { ...plan.hints, next_deed: { deed: 'deed:job', why: 'best' } }
		};
		const text = explain(noFocus, {}, NOW);
		expect(text).toContain('Основных дел нет — дело выбирается по оценке. Следующее дело — работа, лучшее по оценке.');
		expect(text).not.toContain('недоступны');
	});

	it('учёба и конфа в основных делах — счётчики различимы (в игре у обоих значок 📚)', () => {
		const both: Outlook = {
			...plan,
			focus: [
				{ deed: 'deed:learn', today: 1 },
				{ deed: 'deed:confa', today: 2 }
			],
			hints: { ...plan.hints, next_deed: null }
		};
		expect(explain(both, {}, NOW)).toContain('сегодня 📚уч 1, 📚конф 2');
	});

	it('доступного нет — очередь основных по счётчикам: меньше запусков, при равенстве — первое в списке', () => {
		const none: Outlook = { ...plan, hints: { ...plan.hints, next_deed: null } };
		expect(explain(none, {}, NOW)).toContain('Доступных дел сейчас нет; по счётчикам следующее основное — переработка.');
		const even: Outlook = { ...none, focus: plan.focus.map((f) => ({ ...f, today: 2 })) };
		expect(explain(even, {}, NOW)).toContain('по счётчикам следующее основное — добыча');
		expect(explain({ ...none, focus: [] }, {}, NOW)).toMatch(/^Основных дел нет/);
		expect(explain(none, {}, NOW)).toContain('Личное задание: нет данных за сегодня, командное — нет данных за сегодня.');
	});
});
