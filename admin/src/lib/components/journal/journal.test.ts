import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { tick } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApi } from '$lib/api/client';
import type { JournalPage } from '$lib/api/types';
import { JournalFeed } from '$lib/stores/journal.svelte';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import type { LiveEvent } from '$lib/live/sse';
import { deferred, flush } from '$lib/test/deferred';
import JournalView from './JournalView.svelte';
import ActionDetail from './ActionDetail.svelte';
import DecisionDetail from './DecisionDetail.svelte';
import RunSteps from './RunSteps.svelte';

const page = fixture<JournalPage>('journal_page');

async function view(handler: (c: Call) => Response | undefined = () => undefined) {
	const fetch = mockFetch((c) => {
		const custom = handler(c);
		if (custom) return custom;
		if (c.url.startsWith('/api/v1/journal')) return json(page);
		if (c.url === '/api/v1/decisions/344') return json(fixture('decision_detail'));
		if (c.url === '/api/v1/actions/474') return json({ ...fixture<object>('action_detail'), idempotency_key: null, scenario_run_id: 34 });
		if (c.url === '/api/v1/scenario-runs/34')
			return json({
				id: 34, decision_id: 343, scenario: 'sleep', params: { hours: 7 }, started_at: '2026-09-27T19:05:03Z',
				finished_at: '2026-09-27T19:05:12Z', status: 'done', reason: 'fell_asleep', requested_by: null,
				metro_run_id: null,
				actions: [{ ...fixture<object>('action_detail'), idempotency_key: null, scenario_run_id: 34 }]
			});
		return json({ detail: 'Not Found' }, 404);
	});
	const api = createApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, fetch);
	const feed = new JournalFeed(api);
	await feed.reload();
	const confirmer = vi.fn(async () => true);
	render(JournalView, { api, feed, confirmer, now: new Date('2026-09-27T20:27:51Z') });
	return { fetch, feed, confirmer };
}

describe('Журнал на странице с прода', () => {
	it('лента: типы строк и текст — только текстом', async () => {
		await view();
		const list = screen.getByRole('region', { name: 'Лента' });
		const rows = within(list).getAllByRole('button', { pressed: false });
		expect(rows.length).toBeGreaterThanOrEqual(60);
		expect(list).toHaveTextContent('22:47:42 решение ожидание → busy до 28.09 05:05');
		expect(list).toHaveTextContent('сценарий · sleep_Hotel → fell_asleep');
		expect(list).toHaveTextContent('Ты отправился спать красиво в отель на 7 часов за 213 💵');
	});

	it('разбор решения — шторкой', async () => {
		const user = userEvent.setup();
		await view();
		await user.click(screen.getAllByRole('button', { name: /ожидание → busy/ })[0]!);
		const sheet = await screen.findByRole('dialog', { name: 'Решение' });
		expect(await within(sheet).findByText(/Решение #344/)).toBeInTheDocument();
		expect(sheet).toHaveTextContent('Нет (ожидание без выбора)');
		await user.keyboard('{Escape}');
		expect(screen.queryByRole('dialog')).toBeNull();
	});

	it('разбор действия с шагами его запуска', async () => {
		const user = userEvent.setup();
		await view();
		await user.click(screen.getByRole('button', { name: /sleep_Hotel → fell_asleep/ }));
		const sheet = await screen.findByRole('dialog', { name: 'Действие' });
		expect(await within(sheet).findByText(/Действие #474/)).toBeInTheDocument();
		const steps = await within(sheet).findByRole('list', { name: 'Шаги запуска #34' });
		expect(steps).toHaveTextContent('sleep_Hotel');
		expect(sheet).toHaveTextContent('Запуск #34 · sleep');
	});

	it('клик по кнопке сообщения: подтверждение и тот же ключ', async () => {
		const user = userEvent.setup();
		const confirm = {
			code: 'confirm_required', reason: 'missing', confirm_token: 'tok', expires_at: '2026-09-27T20:00:00Z',
			state_version: 1, command_class: 'risky'
		};
		const { fetch, confirmer } = await view((c) => {
			if (c.url !== '/api/v1/commands/click') return undefined;
			return JSON.parse(c.body).confirm_token
				? json({ action_id: 480, status: 'confirmed', reason: 'fell_asleep', answer: null })
				: json({ detail: confirm }, 409);
		});
		const row = screen.getAllByRole('button').find((b) => b.textContent?.includes('Все мы рано или поздно'));
		await user.click(row!);
		const sheet = await screen.findByRole('dialog', { name: 'Сообщение' });
		expect(sheet).toHaveTextContent('Где собираешься спать?');
		await user.click(within(sheet).getByRole('button', { name: 'В отеле - 213 💵' }));
		await vi.waitFor(() => expect(fetch.calls.filter((c) => c.url === '/api/v1/commands/click')).toHaveLength(2));
		const [first, second] = fetch.calls.filter((c) => c.url === '/api/v1/commands/click').map((c) => JSON.parse(c.body));
		expect(first).toMatchObject({ chat_id: 227859379, message_id: 3626304, revision: 1790535904, callback_data: 'sleep_Hotel' });
		expect(second).toEqual({ ...first, confirm_token: 'tok' });
		expect(confirmer).toHaveBeenCalledWith(confirm, 'кнопка «В отеле - 213 💵»');
	});

	it('сообщение изменилось — лента перечитывается', async () => {
		const user = userEvent.setup();
		const { fetch } = await view((c) =>
			c.url === '/api/v1/commands/click'
				? json({ action_id: 481, status: 'rejected', reason: 'stale_revision', answer: null })
				: undefined
		);
		const row = screen.getAllByRole('button').find((b) => b.textContent?.includes('Все мы рано или поздно'));
		await user.click(row!);
		const sheet = await screen.findByRole('dialog', { name: 'Сообщение' });
		const before = fetch.calls.filter((c) => c.url.startsWith('/api/v1/journal')).length;
		await user.click(within(sheet).getByRole('button', { name: 'Под мостом - 0 💵' }));
		await vi.waitFor(() =>
			expect(fetch.calls.filter((c) => c.url.startsWith('/api/v1/journal')).length).toBe(before + 1)
		);
	});
});

describe('Журнал: «сейчас» по общему тикеру', () => {
	afterEach(() => vi.useRealTimers());

	it('после полуночи по МСК вчерашние строки получают дату', async () => {
		vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
		vi.setSystemTime(new Date('2026-09-27T20:59:30Z'));
		const item = { ...page.items[0]!, at: '2026-09-27T20:59:00Z' };
		const fetch = mockFetch(() => json({ items: [item], next_cursor: null }));
		const api = createApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, fetch);
		const feed = new JournalFeed(api);
		await feed.reload();
		render(JournalView, { api, feed });
		const time = screen.getByRole('region', { name: 'Лента' }).querySelector('time')!;
		expect(time).toHaveTextContent(/^23:59:00$/);
		vi.advanceTimersByTime(60_000);
		await tick();
		expect(time).toHaveTextContent('27.09 23:59:00');
	});
});

describe('Шаги идущего запуска', () => {
	const step = { ...fixture<object>('action_detail'), idempotency_key: null, scenario_run_id: 34 };
	const runOf = (status: string, actions: object[]) => ({
		id: 34, decision_id: 343, scenario: 'sleep', params: {}, started_at: '2026-09-27T19:05:03Z',
		finished_at: status === 'running' ? null : '2026-09-27T19:05:30Z', status, reason: '', requested_by: null,
		metro_run_id: null, actions
	});
	const created = (id: number, run: number | null, text: string): LiveEvent => ({
		type: 'action',
		id: `e:${id}`,
		data: {
			id, status: 'intent', reason: '', source: 'scenario', kind: 'send', chat_id: 227859379, text, data: null,
			command_class: 'action', scenario_run_id: run
		}
	});

	it('новый шаг — из кадра создания сразу, запуск перечитывается, пока идёт', async () => {
		const replies = [deferred<Response>(), deferred<Response>(), deferred<Response>()];
		const fetch = mockFetch(() => replies[fetch.calls.length - 1]!.promise);
		const api = createApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, fetch);
		let emit: (e: LiveEvent) => void = () => {};
		const subscribe = (h: (e: LiveEvent) => void) => {
			emit = h;
			return () => {};
		};
		render(RunSteps, { api, runId: 34, subscribe });
		replies[0]!.resolve(json(runOf('running', [step])));
		const steps = await screen.findByRole('list', { name: 'Шаги запуска #34' });
		expect(within(steps).getAllByRole('listitem')).toHaveLength(1);

		emit(created(501, 35, '/other'));
		emit(created(500, 34, '/job'));
		await tick();
		expect(within(steps).getAllByRole('listitem')).toHaveLength(2);
		expect(steps).toHaveTextContent('/job');
		expect(steps).not.toHaveTextContent('/other');
		expect(fetch.calls).toHaveLength(2);

		// Статус шага — из кадра обновления; поздний ответ прежнего чтения не откатывает список.
		emit({ type: 'action', id: 'e:3', data: { id: 500, status: 'refused', reason: 'no_money' } });
		await tick();
		expect(steps).toHaveTextContent('no_money');
		expect(fetch.calls).toHaveLength(3);
		replies[2]!.resolve(json(runOf('done', [step, { ...step, id: 500, status: 'refused', reason: 'no_money', payload: { text: '/job' } }])));
		replies[1]!.resolve(json(runOf('running', [step])));
		await flush();
		expect(within(steps).getAllByRole('listitem')).toHaveLength(2);

		// Завершённый запуск больше не перечитывается по кадрам шагов.
		emit({ type: 'action', id: 'e:4', data: { id: 500, status: 'confirmed', reason: '' } });
		await tick();
		expect(fetch.calls).toHaveLength(3);
	});
});

describe('Ошибки разбора', () => {
	const apiOf = (fetch: typeof globalThis.fetch) =>
		createApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, fetch);

	it('ошибка прежнего решения не показывается у нового', async () => {
		const first = deferred<Response>();
		const fetch = mockFetch((c) => (c.url === '/api/v1/decisions/1' ? first.promise : json(fixture('decision_detail'))));
		const { rerender } = render(DecisionDetail, { api: apiOf(fetch), id: 1 });
		await rerender({ id: 344 });
		expect(await screen.findByText(/Решение #344/)).toBeInTheDocument();
		first.resolve(json({ detail: 'decision not found' }, 404));
		await flush();
		expect(screen.queryByText(/not found/)).toBeNull();
		expect(screen.getByText(/Решение #344/)).toBeInTheDocument();
	});

	it('действие: ошибка сбрасывается после успешной загрузки', async () => {
		let fail = true;
		const fetch = mockFetch(() => (fail ? json({ detail: '' }, 503) : json(fixture('action_detail'))));
		let emit: (e: LiveEvent) => void = () => {};
		render(ActionDetail, {
			api: apiOf(fetch),
			id: 474,
			subscribe: (h: (e: LiveEvent) => void) => {
				emit = h;
				return () => {};
			}
		});
		expect(await screen.findByText('Движок недоступен')).toBeInTheDocument();
		fail = false;
		emit({ type: 'action', id: 'e:1', data: { id: 474, status: 'confirmed', reason: '' } });
		expect(await screen.findByText(/Действие #474/)).toBeInTheDocument();
		expect(screen.queryByText('Движок недоступен')).toBeNull();
	});
});
