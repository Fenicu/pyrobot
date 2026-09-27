import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { createApi } from '$lib/api/client';
import type { LiveEvent } from '$lib/live/sse';
import { json, mockFetch } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import NotificationList from './NotificationList.svelte';
import UnrecognizedList from './UnrecognizedList.svelte';

const NOW = new Date('2026-09-27T20:00:00Z');
const apiWith = (fetch: typeof globalThis.fetch) =>
	createApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, fetch);

describe('Уведомления', () => {
	it('список с прода, «Прочитать всё» до последнего id, живое сверху', async () => {
		const user = userEvent.setup();
		const fetch = mockFetch((c) =>
			c.method === 'POST' ? json({ read: 9 }) : json(fixture('notifications'))
		);
		const onread = vi.fn();
		const listeners: ((e: LiveEvent) => void)[] = [];
		render(NotificationList, {
			api: apiWith(fetch),
			onread,
			now: NOW,
			subscribe: (fn: (e: LiveEvent) => void) => (listeners.push(fn), () => {})
		});
		const list = await screen.findByRole('list', { name: 'Уведомления' });
		expect(await within(list).findByText('mode dry_run -> live by admin')).toBeInTheDocument();
		expect(within(list).getAllByRole('listitem')).toHaveLength(9);
		expect(screen.getByRole('button', { name: 'непрочитанные (9)' })).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Прочитать всё' }));
		await vi.waitFor(() => expect(onread).toHaveBeenCalled());
		expect(JSON.parse(fetch.calls.find((c) => c.method === 'POST')!.body)).toEqual({ up_to_id: 9 });
		// Список перечитан после отметки — дальше живое.
		await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Прочитать всё' })).toBeEnabled());
		listeners.forEach((fn) =>
			fn({ type: 'notification', id: 'e:1', data: { id: 10, level: 'error', code: 'tg_auth_lost', text: '<b>revoked</b>' } })
		);
		await vi.waitFor(() => expect(within(list).getAllByRole('listitem')[0]).toHaveTextContent('tg_auth_lost'));
		const first = within(list).getAllByRole('listitem')[0]!;
		expect(first).toHaveTextContent('<b>revoked</b>');
		expect(first.querySelector('b')).toBeNull();
	});
});

describe('Нераспознанное', () => {
	it('полный текст по клику и отметка разобранного', async () => {
		const user = userEvent.setup();
		const fetch = mockFetch((c) => (c.method === 'POST' ? json({ acked: 2 }) : json(fixture('unrecognized'))));
		render(UnrecognizedList, { api: apiWith(fetch), now: NOW });
		const list = await screen.findByRole('list', { name: 'Нераспознанные сообщения' });
		const row = await within(list).findByRole('button', { name: 'Покупка билетов за 📚' });
		await user.click(row);
		expect(row).toHaveAttribute('aria-expanded', 'true');
		expect(within(list).getByText(/Ты уже купил все доступные за 📚 билеты/)).toHaveClass('ext-text');
		await user.click(screen.getByRole('checkbox', { name: 'Выбрать #9' }));
		await user.click(screen.getByRole('checkbox', { name: 'Выбрать #8' }));
		await user.click(screen.getByRole('button', { name: 'Отметить разобранными (2)' }));
		await vi.waitFor(() => expect(fetch.calls.some((c) => c.method === 'POST')).toBe(true));
		expect(JSON.parse(fetch.calls.find((c) => c.method === 'POST')!.body)).toEqual({ ids: [9, 8] });
		expect(fetch.calls.at(-1)?.url).toContain('acked=false');
	});
});
