import { cleanup, render, screen, waitFor, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AccountOut, OverviewFeed } from '$lib/api/types';
import type { Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { accountOut } from '$lib/test/overview';
import Page from './+page.svelte';

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
		if (call.url.startsWith('/api/v1/overview/feed')) return json(h.body, h.status);
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

const feed = fixture<OverviewFeed>('overview_feed');
const ACCOUNTS: AccountOut[] = [accountOut(1, 'Альфа'), accountOut(2, 'Бета'), accountOut(3, 'Гамма')];
const query = (n: number) => Object.fromEntries(new URL(h.calls[n]!.url, 'http://x').searchParams);

function show(body: unknown = feed, list: AccountOut[] | null = ACCOUNTS) {
	h.body = body;
	h.list = list;
	return render(Page);
}

beforeEach(() => {
	h.calls.length = 0;
});
afterEach(() => {
	cleanup();
	h.status = 200;
});

describe('экран «Лента»', () => {
	it('первая страница: фильтры и события с фикстуры', async () => {
		show();
		expect(await screen.findByRole('list', { name: 'События' })).toBeInTheDocument();
		expect(screen.getAllByRole('listitem')).toHaveLength(feed.items.length);
		expect(query(0)).toEqual({ limit: '50' });
		expect(screen.getByRole('group', { name: 'Типы событий' })).toBeInTheDocument();
	});

	it('тип в фильтре перезапускает ленту с новыми параметрами', async () => {
		const user = userEvent.setup();
		show();
		await screen.findByRole('list', { name: 'События' });
		h.body = { ...feed, items: feed.items.slice(0, 2) };
		await user.click(within(screen.getByRole('group', { name: 'Типы событий' })).getByRole('button', { name: 'Битва' }));
		await waitFor(() => expect(h.calls).toHaveLength(2));
		expect(query(1)).toEqual({ limit: '50', types: 'battle' });
		await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(2));
	});

	it('«Загрузить ещё» идёт по курсору и дописывает хвост', async () => {
		const user = userEvent.setup();
		show();
		await screen.findByRole('list', { name: 'События' });
		h.body = {
			items: [{ ...feed.items[8]!, key: 'l:100', at: '2026-10-15T10:00:00+00:00' }],
			next_cursor: null,
			watermark: null,
			has_more: false
		};
		await user.click(screen.getByRole('button', { name: 'Загрузить ещё' }));
		await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(feed.items.length + 1));
		expect(query(1)).toEqual({ limit: '50', cursor: feed.next_cursor });
		expect(screen.queryByRole('button', { name: 'Загрузить ещё' })).toBeNull();
	});

	it('без следующей страницы кнопки нет', async () => {
		show({ ...feed, next_cursor: null, has_more: false });
		await screen.findByRole('list', { name: 'События' });
		expect(screen.queryByRole('button', { name: 'Загрузить ещё' })).toBeNull();
	});

	it('нет событий: пустое состояние; фильтры остаются', async () => {
		show({ ...feed, items: [], next_cursor: null, has_more: false, watermark: { ledger: 0, notifications: 0 } });
		expect(await screen.findByText('Событий пока нет.')).toBeInTheDocument();
		expect(screen.getByRole('group', { name: 'Типы событий' })).toBeInTheDocument();
	});

	it('нет аккаунтов: ссылка «Открыть аккаунты», ленту не запрашивает', async () => {
		show(feed, []);
		const empty = await screen.findByRole('region', { name: 'Нет аккаунтов' });
		expect(within(empty).getByRole('link', { name: 'Открыть аккаунты' })).toHaveAttribute('href', '/accounts');
		expect(h.calls).toHaveLength(0);
	});

	it('ошибка без данных - сообщение', async () => {
		h.status = 500;
		show({ detail: 'boom' });
		expect(await screen.findByRole('alert')).toHaveTextContent('Лента недоступна');
	});

	it('опрос останавливается при уходе с экрана', async () => {
		vi.useFakeTimers({ toFake: ['setInterval', 'setTimeout', 'clearInterval', 'clearTimeout'] });
		try {
			const view = show();
			await vi.advanceTimersByTimeAsync(0);
			expect(h.calls).toHaveLength(1);
			h.body = { ...feed, items: [], has_more: false };
			await vi.advanceTimersByTimeAsync(30_000);
			expect(query(1).after).toBe('48211:9120');
			view.unmount();
			await vi.advanceTimersByTimeAsync(300_000);
			expect(h.calls).toHaveLength(2);
		} finally {
			vi.useRealTimers();
		}
	});
});
