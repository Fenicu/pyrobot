import { cleanup, render, screen, within } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MetroLive, Outlook, StateOut } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import { liveFrame } from '$lib/test/metro-live';
import MetroLiveCard from './MetroLiveCard.svelte';
import PlanCard from './PlanCard.svelte';

const NOW = new Date('2026-10-07T18:10:00Z');
const TITLE = 'Метро — прохождение';

function card(
	frame: MetroLive | null,
	over: { receivedAt?: number; metroRunning?: boolean; metroRunId?: number | null; now?: Date } = {}
) {
	render(MetroLiveCard, {
		frame,
		receivedAt: over.receivedAt ?? NOW.getTime(),
		now: over.now ?? NOW,
		account: 1,
		metroRunning: over.metroRunning ?? false,
		metroRunId: over.metroRunId ?? null
	});
	return screen.queryByRole('region', { name: TITLE });
}

describe('«Метро — прохождение» на главной', () => {
	beforeEach(() => vi.useFakeTimers({ now: NOW, toFake: ['Date'] }));
	afterEach(() => vi.useRealTimers());

	it('идущий забег: карта, шаги, режим, полоски, найденное, события', () => {
		const block = card(liveFrame())!;
		expect(block).toHaveAttribute('id', 'metro-live');
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

	it('конец забега: итог, найденное и ссылка на забеги', () => {
		const block = card(liveFrame({ running: false, outcome: 'finished', mode: 'leave', found: { money: 157, burger: 1 } }))!;
		expect(block).toHaveTextContent('Забег завершён: вышел сам');
		expect(within(block).getByText('Найдено').parentElement).toHaveTextContent('💵 157 🍔 1');
		expect(within(block).getByRole('link', { name: 'повтор' })).toHaveAttribute('href', '/a/1/metro');
		expect(within(block).queryByRole('progressbar', { name: 'Время забега' })).toBeNull();
		expect(block).not.toHaveTextContent('итог прошлого забега');
	});

	it('итог прошлого забега — только когда известен новый запуск метро', () => {
		const done = liveFrame({ running: false, outcome: 'finished', mode: 'leave' });
		const block = card(done, { metroRunning: true, metroRunId: 8 })!;
		expect(block).toHaveTextContent('Забег завершён: вышел сам');
		expect(block).toHaveTextContent('Это итог прошлого забега');
	});

	it('пауза и продолжение в том же сообщении — не «прошлый забег»; строка «остановлен»', () => {
		const paused = liveFrame({ running: false, outcome: 'paused' });
		const block = card(paused, { metroRunning: true, metroRunId: 8 })!;
		expect(block).toHaveTextContent('Забег остановлен: пауза');
		expect(block).not.toHaveTextContent('Забег завершён');
		expect(block).not.toHaveTextContent('итог прошлого забега');
		expect(block).toHaveTextContent('Забег продолжается: карта обновится с первым шагом.');
	});

	it('прерван перезапуском — остановка, не сбой', () => {
		const block = card(liveFrame({ running: false, outcome: 'cancelled' }))!;
		expect(block).toHaveTextContent('Забег остановлен: прерван перезапуском');
		expect(block).not.toHaveTextContent('сбой');
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

	it('план ещё держит метро, а нового запуска не видно — мягко: «последний забег»', () => {
		const done = liveFrame({ running: false, outcome: 'finished', mode: 'leave' });
		const block = card(done, { metroRunning: true, metroRunId: 7 })!;
		expect(block).not.toHaveTextContent('итог прошлого забега');
		expect(block).toHaveTextContent('Последний забег');
	});

	it('кадры не приходят 5 минут — связь потеряна: без времени и выброса, через 30 минут карточки нет', () => {
		const got = NOW.getTime() - 6 * 60_000;
		const block = card(liveFrame(), { receivedAt: got })!;
		expect(block).toHaveTextContent('связь потеряна, данные на 21:04');
		expect(within(block).queryByRole('progressbar', { name: 'Время забега' })).toBeNull();
		expect(block).not.toHaveTextContent('до выброса');
		expect(block).not.toHaveTextContent('обновлено');
		cleanup();
		expect(card(liveFrame(), { receivedAt: NOW.getTime() - 36 * 60_000 })).toBeNull();
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

	it('без кадра и с итогом старше 30 минут — карточки нет', () => {
		expect(card(null)).toBeNull();
		// Забег с 17:00, конец — 17:10 по доле бюджета: час назад.
		const old = liveFrame({ running: false, outcome: 'finished', mode: 'leave', started_at: '2026-10-07T17:00:00+00:00' });
		expect(card(old)).toBeNull();
	});
});

describe('«План бота»: идущее метро — ссылка на карточку', () => {
	const plan = fixture<Outlook>('outlook');
	const prod = fixture<StateOut>('state');
	const metro: Outlook = { ...plan, loop: { ...plan.loop, current: 'metro', current_params: {} } };

	beforeEach(() => vi.useFakeTimers({ now: new Date(plan.now), toFake: ['Date'] }));
	afterEach(() => vi.useRealTimers());

	it('карточка метро показана — строка «Идёт сценарий» ведёт к ней', () => {
		render(PlanCard, { plan: metro, error: null, state: prod.state, now: new Date(plan.now), metroHref: '#metro-live' });
		const now = screen.getByRole('region', { name: 'Сейчас' });
		expect(within(now).getByRole('link', { name: '▶ Идёт сценарий: 🚇 метро' })).toHaveAttribute('href', '#metro-live');
	});

	it('карточки нет или идёт не метро — без ссылки', () => {
		render(PlanCard, { plan: metro, error: null, state: prod.state, now: new Date(plan.now) });
		const now = screen.getByRole('region', { name: 'Сейчас' });
		expect(now).toHaveTextContent('▶ Идёт сценарий: 🚇 метро');
		expect(within(now).queryByRole('link')).toBeNull();
	});
});
