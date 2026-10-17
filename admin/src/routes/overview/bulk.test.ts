import { cleanup, render, screen, waitFor, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AccountOut, OverviewAccount } from '$lib/api/types';
import { editableLeaves } from '$lib/overview/bulk';
import type { Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { accountOut } from '$lib/test/overview';
import schemaJson from '$lib/settings/settings.schema.json';
import type { JsonSchema } from '$lib/settings/schema';
import { dialogs } from '$lib/stores/confirm.svelte';
import Page from './+page.svelte';

const goto = vi.hoisted(() => vi.fn(async () => {}));
vi.mock('$app/navigation', () => ({ goto }));

const h = vi.hoisted(() => ({
	extra: null as unknown,
	list: null as unknown,
	calls: [] as Call[],
	engine: { results: [] } as unknown,
	settings: { results: [] } as unknown,
	settingsStatus: 200,
	loads: 0
}));

vi.mock('$lib/app.svelte', async () => {
	const { createApi } = await import('$lib/api/client');
	const { json, mockFetch } = await import('$lib/test/fetch');
	const hooks = { csrf: () => 'tok', refreshCsrf: async () => null, unauthorized: () => {} };
	const fetch = mockFetch((call) => {
		h.calls.push(call);
		if (call.url === '/api/v1/overview/accounts') return json(h.extra);
		if (call.url === '/api/v1/overview/engine') return json(h.engine);
		if (call.url === '/api/v1/overview/settings') return json(h.settings, h.settingsStatus);
		const source = /^\/api\/v1\/accounts\/(\d+)\/settings$/.exec(call.url);
		if (source) return json({ version: 1, values: { food: { banana_reserve: 77 } }, defaults: {}, schema: {}, bounds: {} });
		return json({ detail: 'not found' }, 404);
	});
	return {
		api: createApi(hooks, fetch),
		accounts: {
			get list() {
				return h.list;
			},
			error: null,
			load: async () => {
				h.loads += 1;
			}
		}
	};
});

const extras = fixture<OverviewAccount[]>('overview_accounts');
const ACCOUNTS: AccountOut[] = [
	accountOut(1, 'Альфа', { level: 31 }),
	accountOut(2, 'Бета', { level: 12 }),
	accountOut(3, 'Гамма', { level: 4 }),
	accountOut(4, 'Дельта', { status: 'deleting' })
];
const leaves = editableLeaves(schemaJson as unknown as JsonSchema);
const labelOf = (path: string) => leaves.find((l) => l.path === path)!.label;

const posts = () => h.calls.filter((c) => c.method === 'POST');
const rowBox = (name: string) => within(screen.getByRole('table')).getByRole('checkbox', { name: `Выбрать ${name}` });

async function show() {
	h.list = ACCOUNTS;
	h.extra = extras;
	render(Page);
	await screen.findByRole('table');
}

beforeEach(() => {
	localStorage.clear();
	h.calls.length = 0;
	h.loads = 0;
	h.engine = { results: [] };
	h.settings = { results: [] };
	h.settingsStatus = 200;
	goto.mockClear();
	// Битва из данных далеко: предупреждения о паузе нет.
	vi.useFakeTimers({ toFake: ['Date'], now: new Date('2020-01-01T00:00:00Z') });
});
afterEach(() => {
	cleanup();
	vi.useRealTimers();
	dialogs.answer(null);
});

describe('выбор строк', () => {
	it('«выбрать все» отмечает все строки, кроме удаляемых; панель показывает число', async () => {
		const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
		await show();
		expect(screen.queryByRole('region', { name: 'Действия с выбранными' })).toBeNull();
		await user.click(within(screen.getByRole('table')).getByRole('checkbox', { name: 'Выбрать все' }));
		for (const name of ['Альфа', 'Бета', 'Гамма']) expect(rowBox(name)).toBeChecked();
		expect(rowBox('Дельта')).toBeDisabled();
		const bar = screen.getByRole('region', { name: 'Действия с выбранными' });
		expect(bar).toHaveTextContent('Выбрано 3');
		await user.click(within(bar).getByRole('button', { name: 'Снять выбор' }));
		expect(screen.queryByRole('region', { name: 'Действия с выбранными' })).toBeNull();
		expect(rowBox('Альфа')).not.toBeChecked();
	});

	it('щелчок по строке переходит к аккаунту и не выбирает; щелчок по флажку не переходит', async () => {
		const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
		await show();
		await user.click(rowBox('Бета'));
		expect(goto).not.toHaveBeenCalled();
		expect(rowBox('Бета')).toBeChecked();
		await user.click(rowBox('Бета'));
		expect(rowBox('Бета')).not.toBeChecked();
		const cell = within(rowBox('Бета').closest('tr')!).getByTestId('activity');
		await user.click(cell);
		expect(goto).toHaveBeenCalledTimes(1);
		expect(rowBox('Бета')).not.toBeChecked();
	});
});

describe('пауза и продолжение', () => {
	it('«Пауза всех» спрашивает подтверждение, шлёт null и показывает итог по аккаунтам', async () => {
		const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
		await show();
		h.engine = {
			results: [
				{ account_id: 1, status: 'done', reason: null, host: null },
				{ account_id: 2, status: 'unchanged', reason: 'already_paused', host: null },
				{ account_id: 3, status: 'skipped', reason: 'engine_not_running', host: 'locked_elsewhere' }
			]
		};
		await user.click(screen.getByRole('button', { name: 'Пауза всех' }));
		expect(dialogs.current?.title).toBe('Поставить на паузу все аккаунты?');
		expect(posts()).toHaveLength(0);
		dialogs.answer('');
		const dialog = await screen.findByRole('dialog', { name: 'Пауза: результат' });
		expect(JSON.parse(posts()[0]!.body)).toEqual({ action: 'pause', account_ids: null });
		expect(within(dialog).getByRole('region', { name: 'Применено' })).toHaveTextContent('Альфа');
		expect(within(dialog).getByRole('region', { name: 'Не изменилось' })).toHaveTextContent('Бета - уже на паузе');
		expect(within(dialog).getByRole('region', { name: 'Пропущено' })).toHaveTextContent('Гамма - движок не запущен: работает на другом хосте');
		expect(h.loads).toBeGreaterThan(0);
	});

	it('отказ в подтверждении ничего не отправляет', async () => {
		const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
		await show();
		await user.click(screen.getByRole('button', { name: 'Продолжить всех' }));
		dialogs.answer(null);
		expect(posts()).toHaveLength(0);
	});

	it('«Продолжить» у выбранных шлёт их id без подтверждения', async () => {
		const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
		await show();
		await user.click(rowBox('Альфа'));
		await user.click(rowBox('Гамма'));
		await user.click(within(screen.getByRole('region', { name: 'Действия с выбранными' })).getByRole('button', { name: 'Продолжить' }));
		await screen.findByRole('dialog', { name: 'Продолжение: результат' });
		expect(dialogs.current).toBeNull();
		expect(JSON.parse(posts()[0]!.body)).toEqual({ action: 'resume', account_ids: [1, 3] });
	});

	it('за два часа до битвы без выставленной цели пауза предупреждает', async () => {
		vi.setSystemTime(new Date('2026-10-17T08:30:00Z'));
		const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
		await show();
		await user.click(screen.getByRole('button', { name: 'Пауза всех' }));
		expect(dialogs.current?.body).toMatch(/не будет выставлена/);
		dialogs.answer(null);
	});
});

describe('«Настройка…»', () => {
	async function openModal() {
		const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
		await show();
		await user.click(rowBox('Альфа'));
		await user.click(rowBox('Бета'));
		await user.click(screen.getByRole('button', { name: 'Настройка…' }));
		const modal = await screen.findByRole('dialog', { name: 'Настройка у нескольких аккаунтов' });
		return { user, modal };
	}

	it('шлёт ровно выбранный путь, значение и выбранных', async () => {
		const { user, modal } = await openModal();
		expect(within(modal).getByRole('button', { name: 'Применить' })).toBeDisabled();
		await user.type(within(modal).getByRole('searchbox', { name: 'Поиск настройки' }), 'banana_reserve');
		await user.click(within(modal).getByRole('button', { name: new RegExp(labelOf('food.banana_reserve')) }));
		const field = within(modal).getByRole('spinbutton', { name: labelOf('food.banana_reserve') });
		expect(field).toHaveValue(50);
		await user.clear(field);
		await user.type(field, '40');
		await user.click(within(modal).getByRole('checkbox', { name: /Бета/ }));
		h.settings = {
			results: [
				{ account_id: 1, status: 'applied', version: 2, changed: {}, restart_required: [], error: null, warnings: [] }
			]
		};
		await user.click(within(modal).getByRole('button', { name: 'Применить' }));
		const dialog = await screen.findByRole('dialog', { name: 'Настройка: результат' });
		expect(JSON.parse(posts()[0]!.body)).toEqual({
			account_ids: [1],
			changes: { food: { banana_reserve: 40 } },
			confirm_live: false
		});
		expect(within(dialog).getByRole('region', { name: 'Применено' })).toHaveTextContent('Альфа');
		expect(dialogs.current).toBeNull();
	});

	it('значение можно взять у аккаунта', async () => {
		const { user, modal } = await openModal();
		await user.type(within(modal).getByRole('searchbox', { name: 'Поиск настройки' }), 'banana_reserve');
		await user.click(within(modal).getByRole('button', { name: new RegExp(labelOf('food.banana_reserve')) }));
		await user.selectOptions(within(modal).getByRole('combobox', { name: 'Взять у аккаунта' }), 'Гамма');
		await waitFor(() =>
			expect(within(modal).getByRole('spinbutton', { name: labelOf('food.banana_reserve') })).toHaveValue(77)
		);
		expect(h.calls.some((c) => c.url === '/api/v1/accounts/3/settings')).toBe(true);
	});

	it('аккаунты не на этом хосте помечены', async () => {
		const { modal } = await openModal();
		const who = within(modal).getByRole('region', { name: 'У кого' });
		expect(within(who).getByRole('checkbox', { name: /Гамма/ }).closest('label')).toHaveTextContent(
			'изменится в базе, применится при старте'
		);
		expect(who).not.toHaveTextContent('Дельта');
	});

	it('LIVE один раз спрашивает подтверждение и шлёт confirm_live', async () => {
		const { user, modal } = await openModal();
		await user.type(within(modal).getByRole('searchbox', { name: 'Поиск настройки' }), 'engine.mode');
		await user.click(within(modal).getByRole('button', { name: new RegExp(labelOf('engine.mode')) }));
		await user.selectOptions(within(modal).getByRole('combobox', { name: labelOf('engine.mode') }), 'live');
		await user.click(within(modal).getByRole('button', { name: 'Применить' }));
		expect(dialogs.current?.title).toBe('Включить LIVE?');
		expect(posts()).toHaveLength(0);
		dialogs.answer('');
		await screen.findByRole('dialog', { name: 'Настройка: результат' });
		expect(posts()).toHaveLength(1);
		expect(JSON.parse(posts()[0]!.body)).toEqual({
			account_ids: [1, 2],
			changes: { engine: { mode: 'live' } },
			confirm_live: true
		});
	});

	it('ошибка тела (422) остаётся в окне, итогов нет', async () => {
		const { user, modal } = await openModal();
		await user.type(within(modal).getByRole('searchbox', { name: 'Поиск настройки' }), 'banana_reserve');
		await user.click(within(modal).getByRole('button', { name: new RegExp(labelOf('food.banana_reserve')) }));
		h.settingsStatus = 422;
		h.settings = {
			detail: [{ loc: ['body', 'changes', 'food', 'banana_reserve'], msg: 'Input should be a valid integer', type: 'int_parsing' }]
		};
		await user.click(within(modal).getByRole('button', { name: 'Применить' }));
		expect(await within(modal).findByRole('alert')).toBeInTheDocument();
		expect(screen.queryByRole('dialog', { name: 'Настройка: результат' })).toBeNull();
		expect(within(modal).getByRole('button', { name: 'Применить' })).toBeEnabled();
	});
});
