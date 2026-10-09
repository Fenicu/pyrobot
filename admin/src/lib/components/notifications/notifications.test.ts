import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import type { LiveEvent } from '$lib/live/sse';
import { json, mockFetch } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import NotificationsView from './NotificationsView.svelte';
import UnrecognizedList from './UnrecognizedList.svelte';

const NOW = new Date('2026-09-27T20:00:00Z');
const apiWith = (fetch: typeof globalThis.fetch) =>
	createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);

describe('Уведомления', () => {
	it('список с прода, «Прочитать всё» до последнего id, живое сверху', async () => {
		const user = userEvent.setup();
		const fetch = mockFetch((c) =>
			c.method === 'POST'
				? json({ read: 9 })
				: json(fixture(c.url.includes('/unrecognized') ? 'unrecognized' : 'notifications'))
		);
		const onread = vi.fn();
		const listeners: ((e: LiveEvent) => void)[] = [];
		render(NotificationsView, {
			api: apiWith(fetch),
			onread,
			now: NOW,
			subscribe: (fn: (e: LiveEvent) => void) => (listeners.push(fn), () => {})
		});
		const list = await screen.findByRole('list', { name: 'Уведомления' });
		expect(await within(list).findByText('mode dry_run -> live by admin')).toBeInTheDocument();
		expect(within(list).getAllByRole('listitem')).toHaveLength(9);
		expect(screen.getByRole('button', { name: 'непрочитанные (9)' })).toBeInTheDocument();
		const header = screen.getByRole('heading', { level: 1, name: 'Уведомления' }).closest('header')!;
		await user.click(within(header).getByRole('button', { name: 'Прочитать всё' }));
		await vi.waitFor(() => expect(onread).toHaveBeenCalled());
		const post = fetch.calls.find((c) => c.method === 'POST')!;
		expect(post.url).toBe('/api/v1/accounts/1/notifications/read');
		expect(JSON.parse(post.body)).toEqual({ up_to_id: 9 });
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

describe('Уведомления: общая шапка и сетка', () => {
	it('уведомления и нераспознанное — две карточки на одной странице, без вкладок', async () => {
		const fetch = mockFetch((c) => json(fixture(c.url.includes('/unrecognized') ? 'unrecognized' : 'notifications')));
		render(NotificationsView, { api: apiWith(fetch), now: NOW });
		expect(screen.queryByRole('tablist')).toBeNull();
		const notes = screen.getByRole('region', { name: 'Уведомления' });
		const unknown = screen.getByRole('region', { name: 'Нераспознанное' });
		expect(within(notes).getByRole('list', { name: 'Уведомления' })).toBeInTheDocument();
		expect(await within(unknown).findByRole('button', { name: 'Покупка билетов за 📚' })).toBeInTheDocument();
		expect(notes.compareDocumentPosition(unknown) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		expect(within(unknown).getByRole('button', { name: /Отметить разобранными/ })).toBeInTheDocument();
		const header = screen.getByRole('heading', { level: 1, name: 'Уведомления' }).closest('header')!;
		expect(header).not.toContainElement(notes);
		// Непрочитанных нет, пока список не загружен — кнопка недоступна; после загрузки — доступна.
		const readAll = within(header).getByRole('button', { name: 'Прочитать всё' });
		await vi.waitFor(() => expect(readAll).toBeEnabled());
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
