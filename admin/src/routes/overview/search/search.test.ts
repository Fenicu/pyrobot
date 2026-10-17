import { cleanup, render, screen, waitFor } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { goto } from '$app/navigation';
import type { AccountOut, SearchOut } from '$lib/api/types';
import type { Call } from '$lib/test/fetch';
import { accountOut } from '$lib/test/overview';
import { page } from '$lib/test/page.svelte';
import Page from './+page.svelte';

vi.mock('$app/navigation', () => ({
	goto: vi.fn(),
	replaceState: vi.fn()
}));

vi.mock('$app/state', async () => ({
	page: (await import('$lib/test/page.svelte')).page
}));

const h = vi.hoisted(() => ({
	body: null as unknown,
	status: 200,
	calls: [] as Call[],
	list: null as unknown
}));

vi.mock('$lib/app.svelte', async () => {
	const { createApi } = await import('$lib/api/client');
	const { json, mockFetch } = await import('$lib/test/fetch');
	const hooks = { csrf: () => 'tok', refreshCsrf: async () => null, unauthorized: () => {} };
	const fetch = mockFetch((call) => {
		h.calls.push(call);
		if (call.url.startsWith('/api/v1/overview/search')) return json(h.body, h.status);
		return json({ detail: 'not found' }, 404);
	});
	return {
		api: createApi(hooks, fetch),
		accounts: {
			get list() {
				return h.list;
			},
			error: null,
			load: async () => {}
		}
	};
});

const sampleResult: SearchOut = {
	q: 'маска',
	where: 'db',
	accounts: [
		{ account_id: 1, name: 'Альфа', status: 'ok', reason: null, count: 2 },
		{ account_id: 2, name: 'Бета', status: 'unavailable', reason: 'tg_offline', count: 0 }
	],
	hits: [
		{
			account_id: 1,
			chat_id: 100,
			msg_id: 10,
			date: '2026-10-17T12:00:00Z',
			outgoing: false,
			text: 'Ты надел маску 100\nВторая строка',
			markup: null
		},
		{
			account_id: 1,
			chat_id: 100,
			msg_id: 11,
			date: '2026-10-17T12:05:00Z',
			outgoing: true,
			text: 'Ты надел маску 200\nДругая строка',
			markup: null
		}
	]
};

const ACCOUNTS: AccountOut[] = [accountOut(1, 'Альфа'), accountOut(2, 'Бета')];
const query = (n: number) => Object.fromEntries(new URL(h.calls[n]!.url, 'http://x').searchParams);

function open(pathname = '/overview/search', body: unknown = sampleResult, list: AccountOut[] | null = ACCOUNTS) {
	page.params = {};
	page.url = new URL(pathname, 'http://app.invalid');
	h.body = body;
	h.list = list;
	return render(Page);
}

beforeEach(() => {
	h.calls.length = 0;
	h.status = 200;
	vi.clearAllMocks();
});

afterEach(cleanup);

describe('экран «Поиск»', () => {
	it('отображает форму поиска и кнопку', () => {
		open();
		expect(screen.getByPlaceholderText('Текст из игры, например: надел маску')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Искать' })).toBeInTheDocument();
	});

	it('отправка формы выполняет поиск и обновляет URL через replaceState', async () => {
		const user = userEvent.setup();
		open();

		const input = screen.getByPlaceholderText('Текст из игры, например: надел маску');
		await user.type(input, 'маска');

		const submitBtn = screen.getByRole('button', { name: 'Искать' });
		await user.click(submitBtn);

		await waitFor(() => expect(h.calls).toHaveLength(1));
		expect(query(0)).toEqual({
			q: 'маска',
			where: 'db',
			limit: '50'
		});

		expect(goto).toHaveBeenCalledWith(
			expect.stringContaining('/overview/search?q=%D0%BC%D0%B0%D1%81%D0%BA%D0%B0&where=db'),
			expect.objectContaining({ replaceState: true })
		);

		// Проверяем отображение статусов и результатов
		expect(await screen.findByText('Альфа: 2')).toBeInTheDocument();
		expect(screen.getByText('Бета: Telegram не в сети')).toBeInTheDocument();
		expect(screen.getByText('Ты надел маску 100')).toBeInTheDocument();
	});

	it('при открытии с параметром ?q= в URL автоматически запускает поиск', async () => {
		open('/overview/search?q=маска&where=telegram&accounts=1');
		await waitFor(() => expect(h.calls).toHaveLength(1));
		expect(query(0)).toEqual({
			q: 'маска',
			where: 'telegram',
			account_ids: '1',
			limit: '50'
		});
		expect(await screen.findByText('Альфа: 2')).toBeInTheDocument();
	});

	it('при ошибке API показывает сообщение об ошибке', async () => {
		const user = userEvent.setup();
		h.status = 500;
		h.body = { detail: 'Database error' };
		open();

		const input = screen.getByPlaceholderText('Текст из игры, например: надел маску');
		await user.type(input, 'маска');
		await user.click(screen.getByRole('button', { name: 'Искать' }));

		await waitFor(() => expect(h.calls).toHaveLength(1));
		expect(await screen.findByRole('alert')).toBeInTheDocument();
	});

	it('переключатель «Списком» разворачивает плоский список', async () => {
		const user = userEvent.setup();
		open('/overview/search?q=маска');
		await screen.findByText('Альфа: 2');

		const toggleBtn = screen.getByRole('button', { name: /списком/i });
		await user.click(toggleBtn);

		const hits = screen.getAllByTestId('search-hit');
		expect(hits).toHaveLength(2);
	});
});
