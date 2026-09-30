import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { tick } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import type { JournalPage } from '$lib/api/types';
import { JournalFeed } from '$lib/stores/journal.svelte';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { journalWithRuns } from '$lib/test/journal';
import type { LiveEvent } from '$lib/live/sse';
import { deferred, flush } from '$lib/test/deferred';
import JournalView from './JournalView.svelte';
import ActionDetail from './ActionDetail.svelte';
import DecisionDetail from './DecisionDetail.svelte';
import RunSteps from './RunSteps.svelte';
import { actionCommand } from '$lib/util/game';

const page = fixture<JournalPage>('journal_page');

async function view(handler: (c: Call) => Response | undefined = () => undefined) {
	const fetch = mockFetch((c) => {
		const custom = handler(c);
		if (custom) return custom;
		if (c.url.startsWith('/api/v1/accounts/1/journal')) return json(page);
		if (c.url === '/api/v1/accounts/1/decisions/344') return json(fixture('decision_detail'));
		if (c.url === '/api/v1/accounts/1/actions/474') return json({ ...fixture<object>('action_detail'), idempotency_key: null, scenario_run_id: 34 });
		if (c.url === '/api/v1/accounts/1/scenario-runs/34')
			return json({
				id: 34, decision_id: 343, scenario: 'sleep', params: { hours: 7 }, started_at: '2026-09-27T19:05:03Z',
				finished_at: '2026-09-27T19:05:12Z', status: 'done', reason: 'fell_asleep', requested_by: null,
				metro_run_id: null,
				actions: [{ ...fixture<object>('action_detail'), idempotency_key: null, scenario_run_id: 34 }]
			});
		return json({ detail: 'Not Found' }, 404);
	});
	const api = createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
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
			if (c.url !== '/api/v1/accounts/1/commands/click') return undefined;
			return JSON.parse(c.body).confirm_token
				? json({ action_id: 480, status: 'confirmed', reason: 'fell_asleep', answer: null })
				: json({ detail: confirm }, 409);
		});
		const row = screen.getAllByRole('button').find((b) => b.textContent?.includes('Все мы рано или поздно'));
		await user.click(row!);
		const sheet = await screen.findByRole('dialog', { name: 'Сообщение' });
		expect(sheet).toHaveTextContent('Где собираешься спать?');
		await user.click(within(sheet).getByRole('button', { name: 'В отеле - 213 💵' }));
		await vi.waitFor(() => expect(fetch.calls.filter((c) => c.url === '/api/v1/accounts/1/commands/click')).toHaveLength(2));
		const [first, second] = fetch.calls.filter((c) => c.url === '/api/v1/accounts/1/commands/click').map((c) => JSON.parse(c.body));
		expect(first).toMatchObject({ chat_id: 227859379, message_id: 3626304, revision: 1790535904, callback_data: 'sleep_Hotel' });
		expect(second).toEqual({ ...first, confirm_token: 'tok' });
		expect(confirmer).toHaveBeenCalledWith(confirm, 'кнопка «В отеле - 213 💵»');
	});

	it('сообщение изменилось — лента перечитывается', async () => {
		const user = userEvent.setup();
		const { fetch } = await view((c) =>
			c.url === '/api/v1/accounts/1/commands/click'
				? json({ action_id: 481, status: 'rejected', reason: 'stale_revision', answer: null })
				: undefined
		);
		const row = screen.getAllByRole('button').find((b) => b.textContent?.includes('Все мы рано или поздно'));
		await user.click(row!);
		const sheet = await screen.findByRole('dialog', { name: 'Сообщение' });
		const before = fetch.calls.filter((c) => c.url.startsWith('/api/v1/accounts/1/journal')).length;
		await user.click(within(sheet).getByRole('button', { name: 'Под мостом - 0 💵' }));
		await vi.waitFor(() =>
			expect(fetch.calls.filter((c) => c.url.startsWith('/api/v1/accounts/1/journal')).length).toBe(before + 1)
		);
	});
});

describe('Журнал: хроника по запускам', () => {
	it('запуск сценария — одна строка: название, ответ игры, раскрывается шагами', async () => {
		const user = userEvent.setup();
		await view((c) => (c.url.startsWith('/api/v1/accounts/1/journal') ? json(journalWithRuns()) : undefined));
		const list = screen.getByRole('region', { name: 'Лента' });
		const sleep = within(list).getByRole('button', { name: /🛌 сон — Ты отправился спать красиво/ });
		expect(sleep).toHaveTextContent('22:05:03');
		expect(sleep).toHaveTextContent('3 команды · 4 сообщения');
		expect(sleep).toHaveAttribute('aria-expanded', 'false');
		// Шаги и ответы игры не дублируются отдельными строками.
		expect(within(list).queryByRole('button', { name: /sleep_Hotel → fell_asleep/ })).toBeNull();
		await user.click(sleep);
		expect(sleep).toHaveAttribute('aria-expanded', 'true');
		const steps = within(list).getByRole('list', { name: 'Шаги: 🛌 сон' });
		expect(within(steps).getAllByRole('button')).toHaveLength(8);
		await user.click(within(steps).getByRole('button', { name: /sleep_Hotel → fell_asleep/ }));
		expect(await screen.findByRole('dialog', { name: 'Действие' })).toBeInTheDocument();
	});

	it('над первой строкой суток — день по МСК', async () => {
		const page = journalWithRuns();
		// Последняя запись страницы — на сутки раньше.
		const last = page.items.at(-1)!;
		page.items[page.items.length - 1] = { ...last, at: '2026-09-26T18:00:00Z' } as typeof last;
		await view((c) => (c.url.startsWith('/api/v1/accounts/1/journal') ? json(page) : undefined));
		const list = screen.getByRole('region', { name: 'Лента' });
		expect(within(list).getAllByRole('heading', { level: 3 }).map((h) => h.textContent)).toEqual([
			'Сегодня, 27.09',
			'26.09 сб'
		]);
	});

	it('с фильтром по типу — плоская лента', async () => {
		const user = userEvent.setup();
		await view((c) => (c.url.startsWith('/api/v1/accounts/1/journal') ? json(journalWithRuns()) : undefined));
		await user.click(screen.getByRole('button', { name: 'действия' }));
		const list = screen.getByRole('region', { name: 'Лента' });
		await vi.waitFor(() => expect(within(list).getByRole('button', { name: /sleep_Hotel → fell_asleep/ })).toBeInTheDocument());
		expect(within(list).queryByRole('button', { name: /🛌 сон/ })).toBeNull();
	});
});

describe('Журнал: «сейчас» по общему тикеру', () => {
	afterEach(() => vi.useRealTimers());

	it('после полуночи по МСК вчерашние строки получают дату', async () => {
		vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
		vi.setSystemTime(new Date('2026-09-27T20:59:30Z'));
		const item = { ...page.items[0]!, at: '2026-09-27T20:59:00Z' };
		const fetch = mockFetch(() => json({ items: [item], next_cursor: null }));
		const api = createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
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
		const api = createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
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
		createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);

	it('ошибка прежнего решения не показывается у нового', async () => {
		const first = deferred<Response>();
		const fetch = mockFetch((c) => (c.url === '/api/v1/accounts/1/decisions/1' ? first.promise : json(fixture('decision_detail'))));
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

describe('Разбор решения: кандидаты', () => {
	it('выбранный — зелёный, как в макете, параметры кандидатов видны', async () => {
		const decision = {
			...fixture<object>('decision_detail'),
			kind: 'act',
			scenario: 'daily_pick',
			candidates: [
				{ scenario: 'daily_pick', params: { task: 'jobMoney_hard' }, score: null, verdict: 'chosen' },
				{ scenario: 'deed:harvest', params: {}, score: 0.02, verdict: 'no_motivation' }
			]
		};
		const fetch = mockFetch(() => json(decision));
		render(DecisionDetail, {
			api: createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch),
			id: 344
		});
		const rows = await screen.findAllByRole('row');
		const chosen = rows.find((r) => r.textContent?.includes('daily_pick'))!;
		expect(chosen).toHaveTextContent('task=jobMoney_hard');
		expect(within(chosen).getByText('выбрано')).toHaveClass('pill-ok');
		const refused = rows.find((r) => r.textContent?.includes('deed:harvest'))!;
		expect(within(refused).getByText('нет 🔥')).toHaveClass('pill-muted');
	});

	it('вердикты — словарём «Плана бота», код — в подсказке; незнакомый — как есть', async () => {
		const decision = {
			...fixture<object>('decision_detail'),
			kind: 'wait',
			candidates: [
				{ scenario: 'deed:job', params: {}, score: 1.4, verdict: 'reserved' },
				{ scenario: 'deeds', params: {}, score: null, verdict: 'stale:motivation' },
				{ scenario: 'deed:walk', params: {}, score: 0.3, verdict: 'brand_new' }
			]
		};
		const fetch = mockFetch(() => json(decision));
		render(DecisionDetail, {
			api: createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch),
			id: 344
		});
		const reserved = await screen.findByText('🔥 в запасе');
		expect(reserved).toHaveAttribute('title', 'reserved');
		expect(screen.getByText('нужно обновить: 🔥')).toHaveAttribute('title', 'stale:motivation');
		expect(screen.getByText('brand_new')).toHaveClass('pill-muted');
	});
});

describe('Строка ленты', () => {
	it('на ПК — одна строка, на телефоне — две, с многоточием: текст без переносов pre-wrap', async () => {
		const fetch = mockFetch(() => json(page));
		const api = createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
		const feed = new JournalFeed(api);
		await feed.reload();
		render(JournalView, { api, feed, now: new Date('2026-09-27T20:27:51Z') });
		const row = screen.getAllByRole('button', { name: /Ты отправился спать/ })[0]!;
		const line = row.querySelector('.line-clamp-2')!;
		expect(line).not.toBeNull();
		expect(line).toHaveClass('md:truncate', 'col-span-2');
		expect(line.querySelector('.ext-text')).toBeNull();
	});
});

describe('Пересылка в журнале', () => {
	const forwardPayload = {
		text: null,
		data: null,
		reply_to: null,
		message_id: 3625831,
		from_chat_id: 227859379,
		chat_title: '☣️ SU',
		expect_content: 'h',
		expect_revision: null
	};
	const forwardAction = {
		...fixture<object>('action_detail'),
		id: 900,
		kind: 'forward',
		chat_id: -1001149209877,
		source: 'planner',
		command_class: 'forward',
		payload: forwardPayload,
		reason: 'sent',
		answer: '4242',
		match_detail: null,
		idempotency_key: 'forward:227859379:3625831',
		scenario_run_id: 35
	};
	const forwardItem = {
		type: 'action',
		id: 900,
		at: '2026-09-27T19:10:00Z',
		source: 'planner',
		kind: 'forward',
		chat_id: -1001149209877,
		command_class: 'forward',
		status: 'confirmed',
		reason: 'sent',
		text: null,
		data: null,
		message_id: 3625831,
		chat_title: '☣️ SU',
		finished_at: '2026-09-27T19:10:01Z'
	};

	it('строка ленты, разбор и шаг запуска — «→ чат команды «…», сообщение #…», а не «—»', async () => {
		const user = userEvent.setup();
		await view((c) => {
			if (c.url.startsWith('/api/v1/accounts/1/journal')) return json({ items: [forwardItem], next_cursor: null });
			if (c.url === '/api/v1/accounts/1/actions/900') return json(forwardAction);
			if (c.url === '/api/v1/accounts/1/scenario-runs/35')
				return json({
					id: 35, decision_id: 345, scenario: 'factory_report', params: {}, started_at: '2026-09-27T19:09:58Z',
					finished_at: '2026-09-27T19:10:02Z', status: 'done', reason: 'won', requested_by: null,
					metro_run_id: null, actions: [forwardAction]
				});
			return undefined;
		});
		const list = screen.getByRole('region', { name: 'Лента' });
		expect(list).toHaveTextContent('план · → чат команды «☣️ SU», сообщение #3625831');
		await user.click(within(list).getByRole('button', { name: /чат команды/ }));
		const sheet = await screen.findByRole('dialog', { name: 'Действие' });
		expect(await within(sheet).findByText(/Действие #900/)).toBeInTheDocument();
		expect(sheet).toHaveTextContent('→ чат команды «☣️ SU», сообщение #3625831');
		expect(sheet).toHaveTextContent('Куда «☣️ SU» · -1001149209877');
		const steps = await within(sheet).findByRole('list', { name: 'Шаги запуска #35' });
		expect(steps).toHaveTextContent('→ чат команды «☣️ SU», сообщение #3625831');
		expect(steps).not.toHaveTextContent('—');
	});

	it('без названия чата — только номер сообщения', () => {
		expect(actionCommand('forward', { message_id: 5 })).toBe('→ чат команды, сообщение #5');
		expect(actionCommand('send', { text: '/job' })).toBe('/job');
		expect(actionCommand('click', { data: 'sleep_Hotel', message_id: 5 })).toBe('sleep_Hotel');
		expect(actionCommand('send', {})).toBe('—');
	});
});
