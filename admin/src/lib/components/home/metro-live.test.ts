import { cleanup, render, screen, within } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MetroLive, Outlook, StateOut } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import { liveFrame } from '$lib/test/metro-live';
import MetroLiveCard from './MetroLiveCard.svelte';
import NowCard from './NowCard.svelte';

const NOW = new Date('2026-10-07T18:10:00Z');
const TITLE = 'Метро — прохождение';

function card(frame: MetroLive | null, over: { receivedAt?: number; now?: Date } = {}) {
	render(MetroLiveCard, { frame, receivedAt: over.receivedAt ?? NOW.getTime(), now: over.now ?? NOW });
	return screen.queryByRole('group', { name: TITLE });
}

describe('«Метро — прохождение» внутри «Сейчас»', () => {
	beforeEach(() => vi.useFakeTimers({ now: NOW, toFake: ['Date'] }));
	afterEach(() => vi.useRealTimers());

	it('идущий забег: карта, шаги, режим, полоски, найденное, события', () => {
		const block = card(liveFrame())!;
		const map = within(block).getByRole('img', { name: 'Карта забега: шагов 2, посещено клеток 3' });
		expect(map.querySelector('[data-role="me"]')).not.toBeNull();
		expect(block).toHaveTextContent('шаг 2 · клетка (1,1) · ~5.2 с/шаг');
		expect(block).toHaveTextContent('обход');
		const stamina = within(block).getByRole('progressbar', { name: 'Выносливость' });
		// 🔋 выше 100% (бафы) — полоса полная, число как есть.
		expect(stamina).toHaveAttribute('aria-valuenow', '100');
		expect(block).toHaveTextContent('🔋 118%');
		const time = within(block).getByRole('progressbar', { name: 'Время забега' });
		expect(time).toHaveAttribute('aria-valuenow', '25');
		expect(block).toHaveTextContent('до выброса ~35 мин');
		expect(block).toHaveTextContent('❤️ 3');
		expect(within(block).getByText('Найдено').parentElement).toHaveTextContent('💵 30');
		const events = within(block).getByRole('list', { name: 'Последние события' });
		const rows = within(events).getAllByRole('listitem');
		expect(rows[0]).toHaveTextContent('NPC (слабый)');
		expect(rows[1]).toHaveTextContent('находка: +💵 30');
		expect(block).not.toHaveTextContent('Забег завершён');
		expect(block).not.toHaveTextContent('обновлено');
	});

	it('уход к выходу — с причиной; кадры замерли дольше 30 с — сколько назад', () => {
		const block = card(liveFrame({ mode: 'leave', leave_reason: 'deadline' }), {
			receivedAt: NOW.getTime() - 45_000
		})!;
		expect(block).toHaveTextContent('к выходу: не успеть до битвы');
		expect(block).toHaveTextContent('обновлено 45 с назад');
	});

	it('без битвы (бюджет неизвестен) — без полосы времени; 🔋 неизвестна — без пустой полосы', () => {
		const blind = liveFrame({ battle_at: null, kick_at: null, budget: { total_s: null, used: 0, step_s: 5.2 }, stamina: null });
		const block = card(blind)!;
		expect(within(block).queryByRole('progressbar', { name: 'Время забега' })).toBeNull();
		expect(block).not.toHaveTextContent('время:');
		expect(block).not.toHaveTextContent('0%');
		expect(within(block).queryByRole('progressbar', { name: 'Выносливость' })).toBeNull();
		expect(block).toHaveTextContent('🔋 —');
	});

	it('кадры не приходят 5 минут — связь потеряна: без времени и выброса', () => {
		const got = NOW.getTime() - 6 * 60_000;
		const block = card(liveFrame(), { receivedAt: got })!;
		expect(block).toHaveTextContent('связь потеряна, данные на 21:04');
		expect(within(block).queryByRole('progressbar', { name: 'Время забега' })).toBeNull();
		expect(block).not.toHaveTextContent('до выброса');
		expect(block).not.toHaveTextContent('обновлено');
	});

	it('выброс прошёл больше 2 минут назад — связь потеряна, даже если кадр свежий', () => {
		const later = new Date('2026-10-07T18:48:00Z');
		const block = card(liveFrame(), { now: later, receivedAt: later.getTime() - 60_000 })!;
		expect(block).toHaveTextContent('связь потеряна, данные на 21:47');
		expect(block).not.toHaveTextContent('выброс вот-вот');
	});

	it('🔋 неизвестна — прочерк без процента', () => {
		const block = card(liveFrame({ stamina: null }))!;
		expect(block).toHaveTextContent('🔋 —');
		expect(block).not.toHaveTextContent('—%');
	});

});

describe('«Сейчас»: идущий забег — карта вместо плана, кончившийся — строка итога над планом', () => {
	const plan = fixture<Outlook>('outlook');
	const prod = fixture<StateOut>('state');
	const running: Outlook = { ...plan, loop: { ...plan.loop, current: 'metro', current_params: {} } };

	function now(
		frame: MetroLive | null,
		over: { receivedAt?: number; plan?: Outlook | null; runId?: number | null } = {}
	) {
		render(NowCard, {
			plan: over.plan === undefined ? running : over.plan,
			error: null,
			state: prod.state,
			now: NOW,
			account: 1,
			metro: { frame, receivedAt: over.receivedAt ?? NOW.getTime(), metroRunId: over.runId ?? null }
		});
		return screen.getByRole('region', { name: 'Сейчас' });
	}

	beforeEach(() => vi.useFakeTimers({ now: NOW, toFake: ['Date'] }));
	afterEach(() => vi.useRealTimers());

	it('забег идёт — «Сейчас» с пометкой «🚇 забег»: карта, без текста плана и «Почему не другое»', () => {
		const block = now(liveFrame());
		expect(within(block).getByText('🚇 забег')).toHaveClass('pill');
		const metro = within(block).getByRole('group', { name: TITLE });
		expect(within(metro).getByRole('img', { name: /Карта забега/ })).toBeInTheDocument();
		expect(block).not.toHaveTextContent('Идёт сценарий');
		expect(block).not.toHaveTextContent('билеты лотереи');
		expect(within(block).queryByRole('region', { name: 'Почему не другое' })).toBeNull();
	});

	it('план ещё грузится, а кадр забега есть — карта сразу', () => {
		const block = now(liveFrame(), { plan: null });
		expect(within(block).getByRole('group', { name: TITLE })).toBeInTheDocument();
		expect(block).not.toHaveTextContent('Загрузка плана');
	});

	it('связь с идущим забегом потеряна — всё ещё карта с пометкой', () => {
		const block = now(liveFrame(), { receivedAt: NOW.getTime() - 6 * 60_000 });
		expect(within(block).getByRole('group', { name: TITLE })).toHaveTextContent('связь потеряна');
		expect(within(block).getByText('🚇 забег')).toBeInTheDocument();
	});

	it('забег кончился — обычный план, над ним строка итога со ссылкой на забеги, без «🚇 забег»', () => {
		const done = liveFrame({ running: false, outcome: 'finished', mode: 'leave', found: { money: 157 } });
		const block = now(done, { plan });
		const line = within(block).getByRole('status', { name: 'Итог забега' });
		expect(line).toHaveTextContent('Забег завершён: вышел сам');
		expect(within(line).getByRole('link', { name: 'забеги →' })).toHaveAttribute('href', '/a/1/metro');
		expect(block.querySelector('p')).toBe(line);
		expect(within(block).queryByRole('group', { name: TITLE })).toBeNull();
		expect(block).not.toHaveTextContent('🚇 забег');
		expect(block).toHaveTextContent('🤑 билеты лотереи (все — max)');
		expect(within(block).getByRole('region', { name: 'Почему не другое' })).toBeInTheDocument();
	});

	it('итог при уже идущем новом запуске — «итог прошлого»; пауза в том же сообщении — «продолжается»', () => {
		const done = liveFrame({ running: false, outcome: 'finished', mode: 'leave' });
		let line = within(now(done, { runId: 8 })).getByRole('status', { name: 'Итог забега' });
		expect(line).toHaveTextContent('Забег завершён: вышел сам · итог прошлого, идёт вход в новый');
		cleanup();
		line = within(now(liveFrame({ running: false, outcome: 'paused' }), { runId: 8 })).getByRole('status', {
			name: 'Итог забега'
		});
		expect(line).toHaveTextContent('Забег остановлен: пауза · забег продолжается');
		cleanup();
		// Тот же запуск или запуск не виден — только итог.
		line = within(now(liveFrame({ running: false, outcome: 'cancelled' }), { runId: 7 })).getByRole('status', {
			name: 'Итог забега'
		});
		expect(line).toHaveTextContent('Забег остановлен: прерван перезапуском');
		expect(line).not.toHaveTextContent('·');
	});

	it('итог, а план ещё не пришёл — строка итога и загрузка плана', () => {
		const block = now(liveFrame({ running: false, outcome: 'finished', mode: 'leave' }), { plan: null });
		expect(within(block).getByRole('status', { name: 'Итог забега' })).toBeInTheDocument();
		expect(block).toHaveTextContent('Загрузка плана…');
	});

	it('без кадра, связь потеряна больше 30 минут назад, итог старше 30 минут — обычный план', () => {
		const plain = (frame: MetroLive | null, receivedAt?: number) => {
			const block = now(frame, { plan, receivedAt });
			expect(within(block).queryByRole('group', { name: TITLE })).toBeNull();
			expect(within(block).queryByRole('status', { name: 'Итог забега' })).toBeNull();
			expect(block).not.toHaveTextContent('🚇 забег');
			expect(block).toHaveTextContent('🤑 билеты лотереи (все — max)');
			cleanup();
		};
		plain(null);
		plain(liveFrame(), NOW.getTime() - 36 * 60_000);
		// Забег с 17:00, конец — 17:10 по доле бюджета: час назад.
		plain(liveFrame({ running: false, outcome: 'finished', mode: 'leave', started_at: '2026-10-07T17:00:00+00:00' }));
	});
});
