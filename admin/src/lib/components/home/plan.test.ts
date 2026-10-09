import { cleanup, render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { tick } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Outlook, StateOut } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import NextCard from './NextCard.svelte';
import NowCard from './NowCard.svelte';

const plan = fixture<Outlook>('outlook');
const prod = fixture<StateOut>('state');
const NOW = new Date(plan.now);

function card(p: Outlook = plan) {
	render(NowCard, { plan: p, error: null, state: prod.state, now: NOW, account: 1 });
	render(NextCard, { plan: p, error: null, now: NOW });
}

describe('«Сейчас» и «Дальше по времени» на фикстуре из бэкенд-теста', () => {
	// Настоящее время — момент плана: карточка сверяет срок сна цикла с настоящими часами.
	beforeEach(() => vi.useFakeTimers({ now: NOW, toFake: ['Date'] }));
	afterEach(() => vi.useRealTimers());

	it('«Сейчас», «Почему не другое», «Готово сейчас», «Дальше по времени»', () => {
		card();
		const now = screen.getByRole('region', { name: 'Сейчас' });
		expect(now).toHaveTextContent('🤑 билеты лотереи (все — max)');
		expect(now).toHaveTextContent('Занят: работа до 19:50');
		expect(now).toHaveTextContent('Держит 🔥: 2 под метро (откроется в 20:21)');
		expect(now).toHaveTextContent('сегодня ⛏ 3, ⚙️→🔩 2). Следующее дело — переработка.');

		// «Почему не другое» и «Готово сейчас» — свёрнутым списком внутри «Сейчас».
		const why = within(now).getByRole('region', { name: 'Почему не другое' });
		expect(within(now).getByText('Почему не другое · 1', { selector: 'summary' })).toBeInTheDocument();
		const rows = within(why).getAllByRole('listitem');
		expect(rows.map((r) => r.dataset.verdict)).toEqual(['busy', 'chosen']);
		expect(rows[0]).toHaveTextContent('🔨 прокачка навыков');
		expect(rows[0]).toHaveTextContent('занят · до 19:50');
		expect(rows[1]).toHaveTextContent('выбрано');

		const ready = within(now).getByRole('region', { name: /Готово сейчас/ });
		expect(ready).toHaveTextContent('на текущем снимке');
		expect(ready).toHaveTextContent('🍊 мандарин');

		const next = screen.getByRole('region', { name: 'Дальше по времени' });
		expect(now).not.toContainElement(next);
		const timers = within(next).getAllByRole('listitem');
		expect(timers).toHaveLength(11);
		expect(timers[0]).toHaveTextContent('19:50');
		expect(timers[0]).toHaveTextContent('Освободится');
		expect(timers[0]).toHaveTextContent('через 20 мин');
		expect(timers[3]).toHaveTextContent('20:21');
		expect(timers[3]).toHaveTextContent('Метро доступно');
		expect(timers[4]).toHaveTextContent('22:05');
		expect(timers[4]).toHaveTextContent('Сон 7 ч под мостом');
		expect(timers[5]).toHaveTextContent('22:31');
		expect(timers[5]).toHaveTextContent('Можно ехать: 🚕 автомобиль');
		expect(timers[8]).toHaveTextContent('Можно снова обновить экран: транспорт');
		expect(timers[10]).toHaveTextContent('Слив налички в акции перед битвой');
		expect(timers[10]).toHaveTextContent('цель 📯Pied Piper');
	});

	it('без запаса 🔥 строки «Держит» нет; отказ из-за запаса — под что он', () => {
		const reserved: Outlook = {
			...plan,
			reserves: [],
			considered: [{ scenario: 'deed:dconv', params: { today: 2 }, score: 1.2, verdict: 'reserved' }, ...plan.considered]
		};
		card(reserved);
		const now = screen.getByRole('region', { name: 'Сейчас' });
		expect(now).not.toHaveTextContent('Держит');
		cleanup();
		card({ ...reserved, reserves: plan.reserves });
		const why = screen.getByRole('region', { name: 'Почему не другое' });
		const row = within(why).getAllByRole('listitem')[0]!;
		expect(row).toHaveAttribute('data-verdict', 'reserved');
		expect(row).toHaveTextContent('⚙️→🔩 переработка');
		expect(row).toHaveTextContent('🔥 в запасе · сегодня 2, под метро');
	});

	it('при паузе решение — условное', () => {
		card({ ...plan, loop: { ...plan.loop, paused: true, ready: 'paused' } });
		const now = screen.getByRole('region', { name: 'Сейчас' });
		expect(now).toHaveTextContent('⏸ Планировщик на паузе');
		expect(now).toHaveTextContent('когда пауза снимется — 🤑 билеты лотереи (все — max)');
	});

	it('идёт сам сценарий решения — одна строка про него, без «после него — …»', () => {
		card({ ...plan, loop: { ...plan.loop, current: 'lottery_buy', current_params: plan.decision.params } });
		const now = screen.getByRole('region', { name: 'Сейчас' });
		expect(now).toHaveTextContent('▶ Идёт сценарий: 🤑 билеты лотереи');
		expect(now).not.toHaveTextContent('после него');
		// Вне свёрнутого «Почему не другое» (там строка «выбрано»).
		expect(within(now).getAllByText(/билеты лотереи/).filter((e) => !e.closest('details'))).toHaveLength(1);
		expect([...now.querySelectorAll('p')].every((p) => p.textContent?.trim())).toBe(true);
	});

	it('цикл спит до таймера: «Сейчас» — ожидание, действие — «тогда»', () => {
		const at = '2026-09-27T16:42:54Z';
		card({ ...plan, loop: { ...plan.loop, next_wake: at, wait_reason: 'book_ready', wake_at: at } });
		const now = screen.getByRole('region', { name: 'Сейчас' });
		expect(now).toHaveTextContent('⏳ ждёт: прочитать книгу — следующий шаг в 19:42');
		expect(now).toHaveTextContent('Тогда: 🤑 билеты лотереи (все — max)');
	});

	it('срок сна цикла наступил — «Сейчас» перерисовано сразу, не дожидаясь тика часов главной', async () => {
		const at = '2026-09-27T16:42:54Z';
		vi.useFakeTimers({ now: NOW, toFake: ['Date', 'setTimeout', 'clearTimeout'] });
		try {
			card({ ...plan, loop: { ...plan.loop, next_wake: at, wait_reason: 'book_ready', wake_at: at } });
			const now = screen.getByRole('region', { name: 'Сейчас' });
			expect(now).toHaveTextContent('⏳ ждёт: прочитать книгу');
			await vi.advanceTimersByTimeAsync(Date.parse(at) - NOW.getTime() - 1_000);
			await tick();
			expect(now).toHaveTextContent('⏳ ждёт: прочитать книгу');
			await vi.advanceTimersByTimeAsync(1_000);
			await tick();
			expect(now).toHaveTextContent('🤑 билеты лотереи (все — max)');
			expect(now).not.toHaveTextContent('ждёт');
			expect(now).not.toHaveTextContent('Тогда');
		} finally {
			vi.useRealTimers();
		}
	});

	it('план пришёл уже после срока сна цикла, часы главной отстают — «Сейчас» без «ждёт»', async () => {
		// Часы главной — 19:30:00, план снят в 19:30:04 со сроком 19:30:05, ответ пришёл в 19:30:06.
		const taken = '2026-09-27T16:30:04Z';
		const at = '2026-09-27T16:30:05Z';
		vi.useFakeTimers({ now: new Date('2026-09-27T16:30:06Z'), toFake: ['Date', 'setTimeout', 'clearTimeout'] });
		try {
			card({ ...plan, now: taken, loop: { ...plan.loop, next_wake: at, wait_reason: 'book_ready', wake_at: at } });
			await tick();
			const now = screen.getByRole('region', { name: 'Сейчас' });
			expect(now).toHaveTextContent('🤑 билеты лотереи (все — max)');
			expect(now).not.toHaveTextContent('ждёт');
		} finally {
			vi.useRealTimers();
		}
	});

	it('во сне таймеры прохода «после пробуждения» — под подзаголовком', () => {
		const until = plan.wakeups[0]!.at;
		const asleep: Outlook = {
			...plan,
			phase: 'asleep',
			busy: { activity: 'sleep_hotel', until },
			decision: { kind: 'wait', scenario: null, params: {}, reason: 'busy', until },
			considered: [],
			also_ready: [],
			wakeups: plan.wakeups.map((t) => ({ ...t, after_wake: t.kind !== 'busy' }))
		};
		card(asleep);
		const next = screen.getByRole('region', { name: 'Дальше по времени' });
		expect(next).toHaveTextContent('Проснётся');
		expect(within(next).getByText('после пробуждения')).toBeInTheDocument();
		expect(screen.getByRole('region', { name: 'Сейчас' })).toHaveTextContent('🛌 ждёт пробуждения — следующий шаг в 19:50');
	});

	it('занятость устарела, цикл спит: решение — на текущих данных, остальное — по последним', () => {
		const stale = fixture<Outlook>('outlook_stale');
		const label = 'по последним данным (профиль — 19:21)';
		render(NowCard, { plan: stale, error: null, state: prod.state, now: new Date(stale.now), account: 1 });
		render(NextCard, { plan: stale, error: null, now: new Date(stale.now) });
		const now = screen.getByRole('region', { name: 'Сейчас' });
		expect(now).toHaveTextContent('⏳ ждёт: прочитать книгу — следующий шаг в 20:07');
		expect(now).toHaveTextContent('Тогда: 🔄 обновить экран (профиль)');
		expect(now).toHaveTextContent('Занятость устарела: работа до 19:40 — уже свободен');

		// Решение — на текущих данных, под подзаголовком — второй проход.
		const why = screen.getByRole('region', { name: 'Почему не другое' });
		const [decision, basis] = within(why).getAllByRole('list');
		expect(within(decision!).getAllByRole('listitem').map((r) => r.dataset.verdict)).toEqual(['stale:busy', 'chosen']);
		expect(within(why).getByRole('heading', { name: label })).toBeInTheDocument();
		const rows = within(basis!).getAllByRole('listitem');
		expect(rows.map((r) => r.dataset.verdict)).toEqual(['cooldown']);
		expect(rows[0]).toHaveTextContent('🤑 билеты лотереи');
		expect(rows[0]).toHaveTextContent('отсрочка · до 20:20');

		const ready = screen.getByRole('region', { name: `Готово сейчас · ${label}` });
		expect(ready).toHaveTextContent('🍊 мандарин');
		expect(ready).toHaveTextContent('⚙️→🔩 переработка');

		const next = screen.getByRole('region', { name: `Дальше по времени · ${label}` });
		expect(within(next).getAllByRole('listitem')[0]).toHaveTextContent('Прочитать книгу');
	});

	it('на телефоне — первые 5 событий, остальное под «Ещё»; «Почему не другое» свёрнуто', async () => {
		const user = userEvent.setup();
		card();
		const timers = within(screen.getByRole('region', { name: 'Дальше по времени' })).getAllByRole('listitem');
		// Раскладку решает CSS: на телефоне скрыто классом hidden, на ПК (md) видно.
		expect(timers.slice(0, 5).every((t) => !t.classList.contains('hidden'))).toBe(true);
		expect(timers.slice(5).every((t) => t.classList.contains('hidden') && t.classList.contains('md:grid'))).toBe(true);
		// Список причин свёрнут (ширина не важна).
		const why = screen.getByRole('region', { name: 'Почему не другое' });
		expect(why.closest('details')).not.toHaveAttribute('open');
		const more = screen.getByRole('button', { name: 'Ещё' });
		expect(more).toHaveAttribute('aria-expanded', 'false');
		expect(more).toHaveClass('md:hidden');
		// aria-controls — id списков, которые кнопка раскрывает (только те, что есть в DOM:
		// на фикстуре нет таймеров «после пробуждения»).
		const controlled = more.getAttribute('aria-controls')!.split(' ');
		expect(controlled).toHaveLength(1);
		expect(document.getElementById(controlled[0]!)).toBe(timers[0]!.parentElement);
		await user.click(more);
		expect(timers.every((t) => !t.classList.contains('hidden'))).toBe(true);
		expect(screen.getByRole('button', { name: 'Свернуть' })).toHaveAttribute('aria-expanded', 'true');
	});

	it('на ПК «Почему не другое» тоже свёрнуто', () => {
		vi.stubGlobal('matchMedia', (query: string) => ({ media: query, matches: query === '(min-width: 768px)' }));
		try {
			card();
			const why = screen.getByRole('region', { name: 'Почему не другое' });
			expect(why.closest('details')).not.toHaveAttribute('open');
		} finally {
			vi.unstubAllGlobals();
		}
	});

	it('таймеров меньше шести — без «Ещё»', () => {
		render(NextCard, { plan: { ...plan, wakeups: plan.wakeups.slice(0, 5) }, error: null, now: NOW });
		expect(screen.queryByRole('button', { name: 'Ещё' })).toBeNull();
	});

	it('ошибка без плана и загрузка', () => {
		const error = { kind: 'engine_down', status: 503, code: 'planner not started' } as const;
		render(NowCard, { plan: null, error, state: {}, now: NOW, account: 1 });
		render(NextCard, { plan: null, error, now: NOW });
		// Тревога — одна, в «Сейчас»; «Дальше по времени» — приглушённо.
		expect(screen.getByRole('alert')).toHaveTextContent('План недоступен: Движок недоступен.');
		expect(screen.getByRole('region', { name: 'Дальше по времени' })).toHaveTextContent('Таймеры недоступны');
		cleanup();
		render(NowCard, { plan: null, error: null, state: {}, now: NOW, account: 1 });
		render(NextCard, { plan: null, error: null, now: NOW });
		expect(screen.getByRole('region', { name: 'Сейчас' })).toHaveTextContent('Загрузка плана…');
		expect(screen.getByRole('region', { name: 'Дальше по времени' })).toHaveTextContent('Загрузка плана…');
	});

	it('движок не запущен — не тревога, а приглушённое пояснение', () => {
		const error = { kind: 'engine_down', status: 503, code: 'engine not running' } as const;
		render(NowCard, { plan: null, error, state: {}, now: NOW, account: 1 });
		render(NextCard, { plan: null, error, now: NOW });
		expect(screen.queryByRole('alert')).toBeNull();
		const note = within(screen.getByRole('region', { name: 'Сейчас' })).getByRole('status');
		expect(note).toHaveTextContent('План недоступен: движок не запущен.');
		expect(note).toHaveClass('text-fg-muted');
	});
});
