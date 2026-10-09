import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import { createApi } from '$lib/api/client';
import type { LiveEvent } from '$lib/live/sse';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import ControlView from './ControlView.svelte';

const RUN = {
	id: 90, decision_id: null, scenario: 'sleep', params: { hours: 7 }, started_at: '2026-09-27T19:31:00Z',
	finished_at: null, status: 'queued', reason: '', requested_by: 'admin'
};

function setup(handler: (c: Call) => Response | undefined = () => undefined) {
	const listeners = new Set<(e: LiveEvent) => void>();
	const fetch = mockFetch((c) => {
		const custom = handler(c);
		if (custom) return custom;
		if (c.url === '/api/v1/scenarios') return json(fixture('scenarios'));
		if (c.url.startsWith('/api/v1/accounts/1/scenario-runs?')) return json({ items: [RUN], next_before: null });
		return json({ detail: 'Not Found' }, 404);
	});
	const hooks = { csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} };
	const api = createAccountApi(hooks, 1, fetch);
	const globalApi = createApi(hooks, fetch);
	const confirmer = vi.fn(async () => true);
	const subscribe = (fn: (e: LiveEvent) => void) => {
		listeners.add(fn);
		return () => listeners.delete(fn);
	};
	render(ControlView, { api, globalApi, subscribe, confirmer, now: new Date('2026-09-27T20:00:00Z') });
	const emit = (e: LiveEvent) => listeners.forEach((fn) => fn(e));
	return { fetch, confirmer, emit };
}

describe('Управление', () => {
	it('каталог: поиск и пометки', async () => {
		const user = userEvent.setup();
		setup();
		const list = await screen.findByRole('list', { name: 'Каталог сценариев' });
		await within(list).findByText('lottery_buy');
		// Пометка — только у исключения: проверенные на настоящих экранах идут без значка.
		expect(within(list).queryByText('серт.')).toBeNull();
		expect(within(list).getAllByText('симуляция')).toHaveLength(1);
		expect(within(list).getByText('симуляция').closest('button')).toHaveTextContent('deed:rob');
		await user.type(screen.getByRole('searchbox', { name: 'Поиск сценария' }), 'deed:');
		const shown = within(list).getAllByRole('button').map((b) => b.textContent);
		expect(shown.every((t) => t?.includes('deed:'))).toBe(true);
	});

	it('форма по ParamSpec: проверка и запуск с одним ключом на отправку', async () => {
		const user = userEvent.setup();
		const { fetch } = setup((c) =>
			c.url === '/api/v1/accounts/1/scenarios/sleep/run' ? json({ scenario_run_id: 91, status: 'queued' }, 202) : undefined
		);
		await user.click(await screen.findByRole('button', { name: /^sleep/ }));
		const runner = screen.getByRole('region', { name: 'sleep' });
		const hours = within(runner).getByLabelText(/hours · целое от 7 до 12/);
		await user.type(hours, '13');
		await user.click(within(runner).getByRole('button', { name: 'Запустить' }));
		expect(within(runner).getByRole('alert')).toHaveTextContent('не больше 12');
		expect(fetch.calls.some((c) => c.url.endsWith('/run'))).toBe(false);
		await user.clear(hours);
		await user.type(hours, '7');
		await user.click(within(runner).getByRole('button', { name: 'Запустить' }));
		await vi.waitFor(() => expect(fetch.calls.some((c) => c.url.endsWith('/run'))).toBe(true));
		const body = JSON.parse(fetch.calls.find((c) => c.url.endsWith('/run'))!.body);
		expect(body.params).toEqual({ hours: 7 });
		expect(body.idempotency_key).toMatch(/^run-[A-Za-z0-9_.:-]+$/);
	});

	it('сценарий без обязательных параметров — пустые params', async () => {
		const user = userEvent.setup();
		const { fetch } = setup((c) =>
			c.url === '/api/v1/accounts/1/scenarios/lottery_buy/run' ? json({ scenario_run_id: 92, status: 'queued' }, 202) : undefined
		);
		await user.click(await screen.findByRole('button', { name: /^lottery_buy/ }));
		await user.click(screen.getByRole('button', { name: 'Запустить' }));
		await vi.waitFor(() => expect(fetch.calls.some((c) => c.url.endsWith('/run'))).toBe(true));
		expect(JSON.parse(fetch.calls.find((c) => c.url.endsWith('/run'))!.body).params).toEqual({});
	});

	it('ручная risky-команда: окно подтверждения и повтор с токеном', async () => {
		const user = userEvent.setup();
		const confirm = {
			code: 'confirm_required', reason: 'missing', confirm_token: 'tok', expires_at: '2026-09-27T20:02:00Z',
			state_version: 5, command_class: 'risky'
		};
		const { fetch, confirmer } = setup((c) => {
			if (c.url !== '/api/v1/accounts/1/commands/send') return undefined;
			return JSON.parse(c.body).confirm_token
				? json({ action_id: 7, status: 'confirmed', reason: 'reply', answer: null })
				: json({ detail: confirm }, 409);
		});
		await user.type(screen.getByRole('textbox', { name: 'Команда' }), '/sells_piper_80');
		await user.click(screen.getByRole('button', { name: 'Отправить' }));
		expect(await screen.findByText('Выполнено: reply')).toBeInTheDocument();
		const sends = fetch.calls.filter((c) => c.url === '/api/v1/accounts/1/commands/send').map((c) => JSON.parse(c.body));
		expect(sends).toHaveLength(2);
		expect(sends[1]).toEqual({ ...sends[0], confirm_token: 'tok' });
		expect(confirmer).toHaveBeenCalledWith(confirm, '/sells_piper_80');
	});

	it('forbidden — понятный текст; pending — проверка тем же ключом', async () => {
		const user = userEvent.setup();
		let pending = true;
		const { fetch } = setup((c) => {
			if (c.url !== '/api/v1/accounts/1/commands/send') return undefined;
			const text = JSON.parse(c.body).text;
			if (text === '/givemoney') return json({ detail: 'forbidden' }, 403);
			return pending
				? json({ action_id: null, status: 'pending', reason: '' }, 202)
				: json({ action_id: 8, status: 'confirmed', reason: 'sent', answer: null });
		});
		const input = screen.getByRole('textbox', { name: 'Команда' });
		await user.type(input, '/givemoney');
		await user.click(screen.getByRole('button', { name: 'Отправить' }));
		expect(await screen.findByText('Команда запрещена: такие не отправляются никогда')).toBeInTheDocument();
		await user.clear(input);
		await user.type(input, '/inv');
		await user.click(screen.getByRole('button', { name: 'Отправить' }));
		await user.click(await screen.findByRole('button', { name: 'Проверить итог' }));
		pending = false;
		const keys = fetch.calls.filter((c) => c.url === '/api/v1/accounts/1/commands/send').map((c) => JSON.parse(c.body).idempotency_key);
		expect(keys[1]).toBe(keys[2]);
	});

	it('последние ручные запуски: живой статус из SSE', async () => {
		const { emit } = setup();
		const list = await screen.findByRole('list', { name: 'Ручные запуски' });
		await within(list).findByText('queued');
		emit({ type: 'scenario_run', id: 'e:1', data: { id: 90, scenario: null, status: 'done', reason: 'fell_asleep' } });
		expect(await within(list).findByText('done · fell_asleep')).toBeInTheDocument();
	});
});

describe('Управление: общая шапка и сетка', () => {
	it('каталог и форма запуска — рядом, ручная команда и запуски — на всю ширину под ними', async () => {
		const user = userEvent.setup();
		setup();
		expect(screen.getByRole('heading', { level: 1, name: 'Управление' })).toBeInTheDocument();
		const catalog = screen.getByRole('region', { name: 'Сценарии' });
		const pair = catalog.parentElement!;
		expect(pair).toHaveClass('lg:grid-cols-[20rem_minmax(0,1fr)]');
		expect(pair).toContainElement(screen.getByText(/Выберите сценарий в каталоге/));
		await user.click(await screen.findByRole('button', { name: /^sleep/ }));
		expect(pair).toContainElement(screen.getByRole('region', { name: 'sleep' }));
		for (const name of ['Ручная команда в игру', 'Последние ручные запуски']) {
			const card = screen.getByRole('region', { name });
			expect(pair).not.toContainElement(card);
			expect(card.parentElement).toBe(pair.parentElement);
		}
	});
});
