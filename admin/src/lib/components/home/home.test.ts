import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import { dialogs } from '$lib/stores/confirm.svelte';
import type { DayOut, EngineStatus, GadgetsState, Observed, StateOut } from '$lib/api/types';
import { json, mockFetch } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import ConfirmDialog from '../ConfirmDialog.svelte';
import CharacterCard from './CharacterCard.svelte';
import ControlsCard from './ControlsCard.svelte';
import GadgetsCard from './GadgetsCard.svelte';
import StatusHeader from './StatusHeader.svelte';
import TodayCard from './TodayCard.svelte';

const prod = fixture<StateOut>('state');
const status = fixture<EngineStatus>('engine_status');
const NOW = new Date(prod.now);

describe('Главная на снимке с прода', () => {
	it('персонаж', () => {
		render(CharacterCard, { state: prod.state, stale: prod.stale, now: NOW });
		const card = screen.getByRole('region', { name: 'Персонаж · ур. 71' });
		// Порог следующего уровня — не шкала: до 72-го осталось 18 155 142 − 17 520 102.
		expect(within(card).getByText('💡 опыт').parentElement).toHaveTextContent('17.52M');
		expect(within(card).getByText('до ур. 72').parentElement).toHaveTextContent('635 040 💡');
		expect(screen.queryByRole('progressbar')).toBeNull();
		expect(card).toHaveTextContent('$47');
		expect(card).toHaveTextContent('0 / 85');
		expect(card).toHaveTextContent('сон в отеле до 28.09 05:05');
		// Деньги устарели по политике свежести (stale с прода).
		expect(within(card).getByText('💵 деньги').parentElement).toHaveTextContent('(устарело)');
	});

	it('опыт набран, уровень не повышен — ждёт повышения', () => {
		const exp = { ...prod.state.exp!, value: 18_200_000 };
		render(CharacterCard, { state: { ...prod.state, exp }, stale: [], now: NOW });
		const card = screen.getByRole('region', { name: 'Персонаж · ур. 71' });
		expect(within(card).getByText('до ур. 72').parentElement).toHaveTextContent('набран — ждёт повышения');
	});

	describe('прогноз до следующего уровня', () => {
		const dayOf = (date: string, delta: number | null, partial = false) =>
			({ day: date, partial, balance: { exp: { delta, covered: delta !== null } } }) as unknown as DayOut;
		const days = [
			dayOf('2026-09-28', 12726, true),
			dayOf('2026-09-27', 15931),
			dayOf('2026-09-26', 16134),
			dayOf('2026-09-25', 16136),
			dayOf('2026-09-24', 15919),
			dayOf('2026-09-23', 17193),
			dayOf('2026-09-22', null)
		];

		it('хвост «≈ N дн. (к ДД.ММ) при темп/сут» у строки до уровня', () => {
			render(CharacterCard, { state: prod.state, stale: [], now: NOW, days });
			const card = screen.getByRole('region', { name: 'Персонаж · ур. 71' });
			expect(within(card).getByText('до ур. 72').parentElement).toHaveTextContent(
				'635 040 💡 · ≈ 39 дн. (к 06.11) при 16.3K/сут'
			);
			expect(within(card).getByText('💡 опыт').parentElement).not.toHaveTextContent('дн.');
		});

		it('без дней прогноза нет', () => {
			render(CharacterCard, { state: prod.state, stale: prod.stale, now: NOW });
			const card = screen.getByRole('region', { name: 'Персонаж · ур. 71' });
			expect(within(card).getByText('до ур. 72').parentElement).not.toHaveTextContent('дн.');
		});

		it('опыт набран — прогноза нет', () => {
			const exp = { ...prod.state.exp!, value: 18_200_000 };
			render(CharacterCard, { state: { ...prod.state, exp }, stale: [], now: NOW, days });
			const card = screen.getByRole('region', { name: 'Персонаж · ур. 71' });
			const row = within(card).getByText('до ур. 72').parentElement;
			expect(row).toHaveTextContent('набран — ждёт повышения');
			expect(row).not.toHaveTextContent('дн.');
		});

		it('пометка устаревания остаётся', () => {
			render(CharacterCard, { state: prod.state, stale: ['exp'], now: NOW, days });
			const card = screen.getByRole('region', { name: 'Персонаж · ур. 71' });
			expect(within(card).getByText('до ур. 72').parentElement).toHaveTextContent('(устарело)');
		});
	});

	it('сегодня', () => {
		render(TodayCard, { state: prod.state, stale: prod.stale, now: NOW });
		const card = screen.getByRole('region', { name: 'Сегодня' });
		expect(card).toHaveTextContent('работа 💵 сложное · ✓ выполнено');
		expect(card).toHaveTextContent('Командное · 🔩 120/120');
		expect(card).toHaveTextContent('Битва 28.09 12:59 📯Pied Piper');
		expect(card).toHaveTextContent('доступно в 28.09 05:20');
		expect(card).toHaveTextContent('бои закончены · снова 28.09 13:10');
	});

	it('кончившееся дело по часам страницы — «уже свободен» в карточке и в шапке', () => {
		// Сон в отеле до 05:05 MSK; часы страницы — 10 минут спустя.
		const later = new Date('2026-09-28T02:15:09Z');
		render(CharacterCard, { state: prod.state, stale: prod.stale, now: later });
		const card = screen.getByRole('region', { name: 'Персонаж · ур. 71' });
		expect(within(card).getByText('Занятость').parentElement).toHaveTextContent('сон в отеле до 05:05 · уже свободен');
		render(StatusHeader, { status, error: null, live: 'open', state: prod.state, now: later });
		expect(screen.getByRole('region', { name: 'Статус' })).toHaveTextContent('сон в отеле до 05:05 · уже свободен');
	});

	it('идущее дело — без «уже свободен»', () => {
		render(StatusHeader, { status, error: null, live: 'open', state: prod.state, now: NOW });
		const header = screen.getByRole('region', { name: 'Статус' });
		expect(header).toHaveTextContent('сон в отеле до 28.09 05:05');
		expect(header).not.toHaveTextContent('уже свободен');
	});

	it('пустой снимок до первого сообщения', () => {
		render(CharacterCard, { state: {}, stale: [], now: NOW });
		expect(screen.getByText(/Снимка ещё нет/)).toBeInTheDocument();
	});

	it('шапка-статус: движок без аренды аккаунта', () => {
		render(StatusHeader, { status: { ...status, lease_ok: false }, error: null, live: 'open', state: prod.state, now: NOW });
		expect(screen.getByRole('region', { name: 'Статус' })).toHaveTextContent('нет аренды аккаунта');
	});

	it('шапка-статус', () => {
		render(StatusHeader, { status, error: null, live: 'open', state: prod.state, now: NOW });
		const header = screen.getByRole('region', { name: 'Статус' });
		expect(header).toHaveTextContent('LIVE');
		expect(header).toHaveTextContent('TG: online');
		expect(header).toHaveTextContent('след. решение 28.09 05:05');
		expect(header).toHaveTextContent('связь есть');
		expect(header).not.toHaveTextContent('нет аренды аккаунта');
	});

	it('шапка: движок не запущен — одна приглушённая метка вместо ложных тревог', () => {
		// Так отвечает GET /engine/status без движка: проверки здоровья — false, TG — stopped.
		const down: EngineStatus = {
			...status,
			running: false,
			status: 'disabled',
			next_wake: null,
			tg: { ...status.tg, state: 'stopped', user_id: null },
			pipeline_healthy: false,
			workers_ok: false,
			lease_ok: false
		};
		render(StatusHeader, { status: down, error: null, live: 'offline', retryIn: 4000, state: prod.state, now: NOW });
		const header = screen.getByRole('region', { name: 'Статус' });
		expect(header).toHaveTextContent('LIVE');
		expect(within(header).getByText('движок не запущен', { selector: '.pill' })).toHaveClass('pill-muted');
		for (const alarm of ['TG:', 'нет аренды аккаунта', 'конвейер нездоров', 'фоновая задача упала', 'нет связи', 'след. решение']) {
			expect(header).not.toHaveTextContent(alarm);
		}
		expect(header.querySelectorAll('.pill-bad')).toHaveLength(0);
	});

	it('шапка: статус Telegram тем же текстом, что на экране Telegram', () => {
		const tg = { ...status.tg, state: 'unauthorized' as const };
		render(StatusHeader, { status: { ...status, tg }, error: null, live: 'open', state: prod.state, now: NOW });
		expect(screen.getByRole('region', { name: 'Статус' })).toHaveTextContent('TG: не выполнен вход');
	});
});

function controls(mode: 'live' | 'dry_run', running = true) {
	const fetch = mockFetch((c) => {
		if (c.method === 'GET') return json({ version: 13, values: {}, defaults: {}, schema: {} });
		if (c.method === 'PATCH')
			return json({ version: 14, values: {}, changed: {}, restart_required: [] });
		return new Response(null, { status: 204 });
	});
	const api = createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
	render(ConfirmDialog);
	render(ControlsCard, { api, status: { ...status, mode, running }, onchange: () => {} });
	return fetch;
}

describe('компания и команда в карточке персонажа', () => {
	const obs = <T>(value: T) => ({ at: prod.now, src: 'screen' as const, value });

	it('строки «Компания» и «Команда»', () => {
		render(CharacterCard, { state: { ...prod.state, company: obs('bmesa') }, stale: [], now: NOW });
		const card = screen.getByRole('region', { name: 'Персонаж · ур. 71' });
		expect(within(card).getByText('Компания').parentElement).toHaveTextContent('☣️ Black Mesa');
		expect(within(card).getByText('Команда').parentElement).toHaveTextContent('[SU]');
	});

	it('незнакомый код компании — сам код', () => {
		render(CharacterCard, { state: { ...prod.state, company: obs('zzz') }, stale: [], now: NOW });
		expect(screen.getByText('Компания').parentElement).toHaveTextContent('zzz');
	});

	it('не в команде или компания неизвестна — строки нет', () => {
		render(CharacterCard, {
			state: { ...prod.state, company: null, team_tag: obs<string | null>(null) },
			stale: [],
			now: NOW
		});
		expect(screen.queryByText('Компания')).toBeNull();
		expect(screen.queryByText('Команда')).toBeNull();
	});

	it('пометка устаревания у строк', () => {
		render(CharacterCard, {
			state: { ...prod.state, company: obs('bmesa') },
			stale: ['company', 'team_tag'],
			now: NOW
		});
		expect(screen.getByText('Компания').parentElement).toHaveTextContent('(устарело)');
		expect(screen.getByText('Команда').parentElement).toHaveTextContent('(устарело)');
	});
});

describe('карточка гаджетов', () => {
	const gadgets = (over: Partial<GadgetsState> = {}): Observed<GadgetsState> => ({
		at: prod.now,
		src: 'screen',
		value: {
			items: [
				{
					grade: '⚫️',
					level: 26,
					slot: '🕶',
					name: 'Хиджаб',
					bonuses: { theory: 85, wisdom: 55, practice: 30 },
					mark: '🧶'
				},
				{
					grade: '🔴',
					level: 18,
					slot: '💻',
					name: 'MAC-адрес ноута',
					bonuses: { theory: 31, cunning: 31 },
					mark: null
				}
			],
			sets: ['⚫️Сет VIP', '🔴Сет Хакер'],
			...over
		}
	});

	it('строка на гаджет, сеты одной строкой', () => {
		render(GadgetsCard, { state: { ...prod.state, gadgets: gadgets() }, stale: [] });
		const card = screen.getByRole('region', { name: 'Гаджеты' });
		const items = within(card).getAllByRole('listitem');
		expect(items.map((li) => li.textContent?.replace(/\s+/g, ' ').trim())).toEqual([
			'🕶 Хиджаб ⚫️26 · +85🎓 +55🐢 +30🔨 🧶',
			'💻 MAC-адрес ноута 🔴18 · +31🎓 +31🐿'
		]);
		expect(card).toHaveTextContent('⚫️Сет VIP · 🔴Сет Хакер');
		expect(card).not.toHaveTextContent('(устарело)');
	});

	it('без сетов строки сетов нет', () => {
		render(GadgetsCard, { state: { ...prod.state, gadgets: gadgets({ sets: [] }) }, stale: [] });
		expect(screen.getByRole('region', { name: 'Гаджеты' })).not.toHaveTextContent('Сет');
	});

	it('пустой список — «ничего не надето»', () => {
		render(GadgetsCard, { state: { ...prod.state, gadgets: gadgets({ items: [], sets: [] }) }, stale: [] });
		expect(screen.getByRole('region', { name: 'Гаджеты' })).toHaveTextContent('ничего не надето');
		expect(screen.queryByRole('listitem')).toBeNull();
	});

	it('данных нет — «нет данных»', () => {
		render(GadgetsCard, { state: { ...prod.state, gadgets: null }, stale: [] });
		expect(screen.getByRole('region', { name: 'Гаджеты' })).toHaveTextContent('нет данных');
	});

	it('устаревшее — с пометкой', () => {
		render(GadgetsCard, { state: { ...prod.state, gadgets: gadgets() }, stale: ['gadgets'] });
		expect(screen.getByRole('region', { name: 'Гаджеты' })).toHaveTextContent('(устарело)');
	});
});

describe('управление', () => {
	beforeEach(() => dialogs.answer(null));

	it('в live — только после подтверждения, с confirm_live', async () => {
		const user = userEvent.setup();
		const fetch = controls('dry_run');
		await user.click(screen.getByRole('button', { name: 'Включить live' }));
		const dialog = await screen.findByRole('dialog', { name: 'Включить LIVE?' });
		expect(dialog).toHaveTextContent('Бот начнёт реально тратить ресурсы');
		await user.click(within(dialog).getByRole('button', { name: 'Отмена' }));
		expect(fetch.calls).toEqual([]);
		await user.click(screen.getByRole('button', { name: 'Включить live' }));
		const again = await screen.findByRole('dialog', { name: 'Включить LIVE?' });
		await user.click(within(again).getByRole('button', { name: 'Включить live' }));
		await vi_wait(() => fetch.calls.some((c) => c.method === 'PATCH'));
		const patch = fetch.calls.find((c) => c.method === 'PATCH');
		expect(JSON.parse(patch?.body ?? '{}')).toEqual({
			version: 13,
			changes: { engine: { mode: 'live' } },
			confirm_live: true
		});
	});

	it('в dry_run — без окна', async () => {
		const user = userEvent.setup();
		const fetch = controls('live');
		await user.click(screen.getByRole('button', { name: 'В dry_run' }));
		expect(screen.queryByRole('dialog')).toBeNull();
		await vi_wait(() => fetch.calls.some((c) => c.method === 'PATCH'));
		const patch = fetch.calls.find((c) => c.method === 'PATCH');
		expect(JSON.parse(patch?.body ?? '{}')).toMatchObject({ confirm_live: false });
	});

	it('движок не запущен: пауза и kill недоступны, режим переключается прямой записью', async () => {
		const user = userEvent.setup();
		const fetch = controls('live', false);
		expect(screen.getByRole('button', { name: 'Пауза' })).toBeDisabled();
		expect(screen.getByRole('button', { name: 'Kill' })).toBeDisabled();
		expect(screen.getByRole('region', { name: 'Управление' })).toHaveTextContent('Пауза и kill — у запущенного движка');
		await user.click(screen.getByRole('button', { name: 'В dry_run' }));
		await vi_wait(() => fetch.calls.some((c) => c.method === 'PATCH'));
	});

	it('у кнопок подсказки при наведении: чем пауза отличается от kill и dry_run', () => {
		controls('live');
		expect(screen.getByRole('button', { name: 'Пауза' })).toHaveAttribute(
			'title',
			expect.stringContaining('ручные команды по умолчанию проходят')
		);
		expect(screen.getByRole('button', { name: 'Kill' })).toHaveAttribute(
			'title',
			expect.stringContaining('в игру не уходит ничего')
		);
		expect(screen.getByRole('button', { name: 'В dry_run' })).toHaveAttribute(
			'title',
			expect.stringContaining('только пишет в журнал')
		);
	});

	it('kill — с причиной', async () => {
		const user = userEvent.setup();
		const fetch = controls('live');
		await user.click(screen.getByRole('button', { name: 'Kill' }));
		await user.type(await screen.findByLabelText('Причина'), 'проверка{Enter}');
		await vi_wait(() => fetch.calls.length > 0);
		expect(fetch.calls[0]).toMatchObject({ method: 'POST', url: '/api/v1/accounts/1/engine/kill' });
		expect(JSON.parse(fetch.calls[0]?.body ?? '')).toEqual({ reason: 'проверка' });
	});
});

async function vi_wait(cond: () => boolean): Promise<void> {
	for (let i = 0; i < 50 && !cond(); i++) await new Promise((r) => setTimeout(r, 5));
	expect(cond()).toBe(true);
}
