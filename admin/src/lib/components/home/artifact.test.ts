import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import type { ArtifactOut, EngineStatus, StateOut } from '$lib/api/types';
import { ArtifactStore } from '$lib/artifact/store.svelte';
import { dialogs } from '$lib/stores/confirm.svelte';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import ConfirmDialog from '../ConfirmDialog.svelte';
import ArtifactCard from './ArtifactCard.svelte';
import StatusHeader from './StatusHeader.svelte';

const NOW = new Date('2026-10-03T09:00:00Z');
const RUN: ArtifactOut['run'] = {
	artifact: null,
	status: 'idle',
	requested_at: null,
	started_at: null,
	ends_at: null,
	deed_hint: null,
	result_level: null,
	lottery_switched: false
};
const IDLE: ArtifactOut = {
	now: NOW.toISOString(),
	run: RUN,
	level: null,
	levels: { book: 100, fax: 100, light: 84 },
	collecting: null,
	external: false,
	tactic: { book: ['learn'], fax: ['job'], light: ['walk'] },
	lottery_on: false,
	lottery_on_start: true,
	pace: { levels_per_day: null, forecast_level: null },
	next_start_at: null
};
const ACTIVE: ArtifactOut = {
	...IDLE,
	run: {
		...RUN,
		artifact: 'light',
		status: 'active',
		started_at: '2026-10-01T09:00:00Z',
		ends_at: '2026-10-11T09:00:00Z',
		deed_hint: 'walk'
	},
	level: 37,
	levels: { ...IDLE.levels, light: 37 },
	collecting: { artifact: 'light', ends_at: '2026-10-11T09:00:00Z' },
	pace: { levels_per_day: 18.5, forecast_level: 100 },
	next_start_at: '2026-10-11T09:00:00Z'
};

function card(artifact: ArtifactOut | null, reply: (c: Call) => Response = () => json(ACTIVE)) {
	const fetch = mockFetch(reply);
	const api = createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
	const onchange = vi.fn();
	render(ConfirmDialog);
	render(ArtifactCard, { api, artifact, error: null, now: NOW, onchange });
	return { fetch, onchange };
}

describe('карточка «Сбор артефакта»', () => {
	beforeEach(() => dialogs.answer(null));

	it('нет сбора: запуск — только для артефактов ниже 100', () => {
		card(IDLE);
		const region = screen.getByRole('region', { name: 'Сбор артефакта' });
		expect(within(region).getByRole('button', { name: 'Запустить сбор: 🔦 Фонарь Sw-ет (84/100)' })).toBeInTheDocument();
		expect(within(region).queryByRole('button', { name: /Букварь/ })).toBeNull();
		expect(within(region).queryByRole('button', { name: /SW факs/ })).toBeNull();
	});

	it('окно запуска: предупреждения, дела, переключатель лотереи — тело запроса с lottery_max', async () => {
		const user = userEvent.setup();
		const { fetch, onchange } = card(IDLE, () => json({ ...IDLE, run: { ...RUN, artifact: 'light', status: 'starting' } }, 202));
		await user.click(screen.getByRole('button', { name: /Запустить сбор: 🔦/ }));
		const dialog = await screen.findByRole('dialog', { name: 'Запустить сбор: 🔦 Фонарь Sw-ет' });
		expect(dialog).toHaveTextContent('Уровень артефакта станет 0');
		expect(dialog).toHaveTextContent('🔥 мотивация обнулится');
		expect(dialog).toHaveTextContent('остановить его в игре нельзя');
		expect(dialog).toHaveTextContent('Части — в делах: 🔥 → прогулка');
		const toggle = within(dialog).getByRole('switch', { name: 'Включить лотерею на максимум' });
		expect(toggle).toBeChecked();
		await user.click(toggle);
		await user.click(within(dialog).getByRole('button', { name: 'Запустить' }));
		await vi.waitFor(() => expect(onchange).toHaveBeenCalledOnce());
		expect(fetch.calls.map((c) => [c.method, c.url, c.body])).toEqual([
			['POST', '/api/v1/accounts/1/artifact/start', JSON.stringify({ artifact: 'light', lottery_max: false })]
		]);
	});

	it('лотерея уже включена — переключателя нет, lottery_max false', async () => {
		const user = userEvent.setup();
		const { fetch } = card({ ...IDLE, lottery_on: true }, () => json(IDLE, 202));
		await user.click(screen.getByRole('button', { name: /Запустить сбор: 🔦/ }));
		const dialog = await screen.findByRole('dialog');
		expect(within(dialog).queryByRole('switch')).toBeNull();
		await user.click(within(dialog).getByRole('button', { name: 'Запустить' }));
		await vi.waitFor(() => expect(fetch.calls).toHaveLength(1));
		expect(JSON.parse(fetch.calls[0]!.body)).toEqual({ artifact: 'light', lottery_max: false });
	});

	it('идёт сбор: прогресс, остаток, темп, дела; пауза', async () => {
		const user = userEvent.setup();
		const { fetch } = card(ACTIVE, () => json({ ...ACTIVE, run: { ...ACTIVE.run, status: 'paused' } }));
		const region = screen.getByRole('region', { name: 'Сбор артефакта' });
		expect(region).toHaveTextContent('🔦 Фонарь Sw-ет');
		expect(region).toHaveTextContent('37/100');
		expect(within(region).getByRole('progressbar', { name: 'Уровень артефакта' })).toHaveAttribute('aria-valuenow', '37');
		expect(region).toHaveTextContent('осталось 8 д');
		expect(region).toHaveTextContent('темп 18.5 ур./сутки · прогноз 100/100');
		expect(region).toHaveTextContent('🔥 → прогулка');
		await user.click(within(region).getByRole('button', { name: 'Пауза' }));
		await vi.waitFor(() => expect(fetch.calls).toHaveLength(1));
		expect(fetch.calls.map((c) => [c.method, c.url])).toEqual([['POST', '/api/v1/accounts/1/artifact/pause']]);
	});

	it('отмена — с подтверждением и датой следующего сбора', async () => {
		const user = userEvent.setup();
		const { fetch } = card(ACTIVE);
		await user.click(screen.getByRole('button', { name: 'Отменить' }));
		const dialog = await screen.findByRole('dialog', { name: 'Отменить сбор?' });
		expect(dialog).toHaveTextContent('Следующий сбор — не раньше 11.10');
		await user.click(within(dialog).getByRole('button', { name: 'Не отменять' }));
		expect(fetch.calls).toEqual([]);
		await user.click(screen.getByRole('button', { name: 'Отменить' }));
		const again = await screen.findByRole('dialog', { name: 'Отменить сбор?' });
		await user.click(within(again).getByRole('button', { name: 'Отменить сбор' }));
		await vi.waitFor(() => expect(fetch.calls).toHaveLength(1));
		expect(fetch.calls.map((c) => c.url)).toEqual(['/api/v1/accounts/1/artifact/cancel']);
	});

	it('итог с блокировкой: без кнопок запуска, дата следующего сбора', () => {
		const done: ArtifactOut = { ...ACTIVE, run: { ...ACTIVE.run, status: 'cancelled', result_level: 37 }, collecting: null };
		card(done);
		const region = screen.getByRole('region', { name: 'Сбор артефакта' });
		expect(region).toHaveTextContent('Итог: 🔦 Фонарь Sw-ет — 37/100 (отменён)');
		expect(region).toHaveTextContent('Следующий сбор — с 11.10');
		expect(within(region).queryByRole('button', { name: /Запустить/ })).toBeNull();
	});

	it('сбор, начатый в игре без бота, — «Вести сбор»', async () => {
		const user = userEvent.setup();
		const outside: ArtifactOut = {
			...IDLE,
			collecting: { artifact: 'fax', ends_at: '2026-10-05T09:00:00Z' },
			external: true,
			next_start_at: '2026-10-05T09:00:00Z'
		};
		const { fetch } = card(outside);
		const region = screen.getByRole('region', { name: 'Сбор артефакта' });
		expect(region).toHaveTextContent('В игре идёт сбор 📠 SW факs');
		await user.click(within(region).getByRole('button', { name: 'Вести сбор' }));
		await vi.waitFor(() => expect(fetch.calls).toHaveLength(1));
		expect(fetch.calls.map((c) => c.url)).toEqual(['/api/v1/accounts/1/artifact/adopt']);
	});

	it('запуск ждёт свободного персонажа — отмена без даты блокировки', async () => {
		const user = userEvent.setup();
		card({ ...IDLE, run: { ...RUN, artifact: 'light', status: 'starting' } });
		expect(screen.getByRole('region', { name: 'Сбор артефакта' })).toHaveTextContent('как только персонаж освободится');
		await user.click(screen.getByRole('button', { name: 'Отменить' }));
		expect(await screen.findByRole('dialog', { name: 'Отменить сбор?' })).toHaveTextContent('в игре сбор ещё не начат');
	});

	it('409 deeds_disabled при запуске — текст ошибки в карточке', async () => {
		const user = userEvent.setup();
		card(IDLE, () => json({ detail: 'deeds_disabled' }, 409));
		await user.click(screen.getByRole('button', { name: /Запустить сбор: 🔦/ }));
		const dialog = await screen.findByRole('dialog');
		await user.click(within(dialog).getByRole('button', { name: 'Запустить' }));
		const region = screen.getByRole('region', { name: 'Сбор артефакта' });
		expect(await within(region).findByRole('alert')).toHaveTextContent(
			'Дела выключены в «Функциях» — сбор не потратит 🔥. Включите дела и запустите снова'
		);
	});
});

describe('стор сбора', () => {
	it('перечитывает на кадр settings и на state с уровнями артефактов', async () => {
		const fetch = mockFetch(() => json(IDLE));
		const api = createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
		const store = new ArtifactStore(api);
		store.start();
		await vi.waitFor(() => expect(store.data).not.toBeNull());
		store.onEvent({ type: 'settings', id: '1', data: { version: 2, mode: 'live', paused: false, killed: false } });
		store.onEvent({ type: 'state', id: '2', data: { version: 3, changed: { money: null } } });
		store.onEvent({ type: 'state', id: '3', data: { version: 4, changed: { artifacts: null } } });
		await vi.waitFor(() => expect(fetch.calls.length).toBe(3));
		store.stop();
	});
});

describe('шапка', () => {
	it('значок сбора с уровнем', () => {
		const prod = fixture<StateOut>('state');
		const status = fixture<EngineStatus>('engine_status');
		const at = NOW.toISOString();
		const state = {
			...prod.state,
			artifacts: { value: { light: 37 }, at, src: 'screen' as const },
			artifact_collect: { value: { artifact: 'light', ends_at: '2026-10-11T09:00:00Z' }, at, src: 'screen' as const }
		};
		render(StatusHeader, { status, error: null, live: 'open', state, now: NOW });
		expect(screen.getByRole('region', { name: 'Статус' })).toHaveTextContent('🔦 37/100');
	});
});
