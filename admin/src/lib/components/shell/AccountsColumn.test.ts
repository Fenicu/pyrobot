import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { tick } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AccountOrderStore, SAVE_FAILED } from '$lib/accounts/order.svelte';
import { createApi } from '$lib/api/client';
import type { AccountOut } from '$lib/api/types';
import { AccountsStore } from '$lib/stores/accounts.svelte';
import { toasts } from '$lib/stores/toasts.svelte';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import AccountsColumn from './AccountsColumn.svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => {}) }));

const hooks = { csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} };
const ORDER = '/api/v1/me/ui/account-order';

const account = (id: number, over: Partial<AccountOut> = {}): AccountOut => ({
	id,
	name: `acc${id}`,
	status: 'enabled',
	status_reason: null,
	blocked: false,
	blocked_reason: null,
	tg: { user_id: id, online: true },
	mode: 'live',
	paused: false,
	killed: false,
	last_action_at: null,
	unread: { warn: 0, error: 0 },
	company: null,
	team_tag: null,
	level: null,
	busy: null,
	in_metro: false,
	alert: null,
	...over
});

async function setup(saved: number[] | null, putStatus = 204) {
	let accounts = [1, 2, 3];
	const fetch = mockFetch((c) => {
		if (c.url === '/api/v1/accounts') return json(accounts.map((id) => account(id)));
		if (c.url === ORDER && c.method === 'GET') return json({ order: saved && { version: 1, ids: saved } });
		if (c.url === ORDER && c.method === 'PUT') return json(putStatus === 204 ? null : { detail: 'boom' }, putStatus);
		return json({ detail: 'x' }, 500);
	});
	const api = createApi(hooks, fetch);
	const order = new AccountOrderStore(api);
	const store = new AccountsStore(api, undefined, order);
	await Promise.all([store.load(), order.load()]);
	render(AccountsColumn, { api, store, current: 1, path: '/a/1' });
	const reload = async (ids: number[]) => {
		accounts = ids;
		await store.load();
		await tick();
	};
	return { fetch, user: userEvent.setup(), reload };
}

const titles = () =>
	within(screen.getByRole('list', { name: 'Список аккаунтов' }))
		.getAllByRole('link')
		.map((a) => a.getAttribute('href'));
const puts = (calls: Call[]) => calls.filter((c) => c.method === 'PUT').map((c) => JSON.parse(c.body));
const handle = (n: number) => screen.getByRole('button', { name: `Перетащить acc${n}` });

afterEach(() => {
	cleanup();
	localStorage.clear();
	toasts.items = [];
});

describe('колонка аккаунтов: порядок', () => {
	it('строки — в сохранённом порядке', async () => {
		await setup([3, 1]);
		expect(titles()).toEqual(['/a/3', '/a/1', '/a/2']);
	});

	it('стрелка вниз на ручке переставляет и сохраняет; фокус остаётся на ручке', async () => {
		const { fetch, user } = await setup(null);
		handle(1).focus();
		await user.keyboard('{ArrowDown}');
		expect(titles()).toEqual(['/a/2', '/a/1', '/a/3']);
		await waitFor(() => expect(puts(fetch.calls)).toEqual([{ version: 1, ids: [2, 1, 3] }]));
		expect(handle(1)).toHaveFocus();
		await user.keyboard('{ArrowUp}{ArrowUp}');
		expect(titles()).toEqual(['/a/1', '/a/2', '/a/3']);
		// Выше первого — некуда: запись не уходит.
		await waitFor(() => expect(puts(fetch.calls)).toHaveLength(2));
	});

	it('запись не прошла — прежний порядок и тост', async () => {
		const { user } = await setup([3, 1, 2], 500);
		handle(3).focus();
		await user.keyboard('{ArrowDown}');
		await waitFor(() => expect(toasts.items.map((t) => t.text)).toEqual([SAVE_FAILED]));
		expect(titles()).toEqual(['/a/3', '/a/1', '/a/2']);
	});

	it('перетаскивание указателем: строка за указателем, на отпускании — новый порядок', async () => {
		const { fetch } = await setup(null);
		const rows = within(screen.getByRole('list', { name: 'Список аккаунтов' })).getAllByRole('listitem');
		rows.forEach((li, i) => {
			li.getBoundingClientRect = () => new DOMRect(0, i * 40, 200, 40);
		});
		const h = handle(1);
		await fireEvent.pointerDown(h, { pointerId: 1, button: 0, pointerType: 'mouse', clientX: 190, clientY: 20 });
		await fireEvent.pointerMove(h, { pointerId: 1, pointerType: 'mouse', clientX: 190, clientY: 70 });
		expect(rows[0]!.style.transform).toBe('translate(0px, 50px)');
		expect(rows[1]!.style.transform).toBe('translate(0px, -40px)');
		expect(rows[2]!.style.transform).toBe('');
		await fireEvent.pointerUp(h, { pointerId: 1, pointerType: 'mouse', clientX: 190, clientY: 70 });
		expect(titles()).toEqual(['/a/2', '/a/1', '/a/3']);
		await waitFor(() => expect(puts(fetch.calls)).toEqual([{ version: 1, ids: [2, 1, 3] }]));
		expect(rows[0]!.style.transform).toBe('');
	});

	it('отмена указателя — без записи; в свёрнутой колонке ручек нет', async () => {
		const { fetch, user } = await setup(null);
		const h = handle(2);
		await fireEvent.pointerDown(h, { pointerId: 1, button: 0, pointerType: 'touch', clientX: 190, clientY: 60 });
		await fireEvent.pointerMove(h, { pointerId: 1, pointerType: 'touch', clientX: 190, clientY: 200 });
		await fireEvent.pointerCancel(h, { pointerId: 1, pointerType: 'touch' });
		expect(titles()).toEqual(['/a/1', '/a/2', '/a/3']);
		expect(puts(fetch.calls)).toEqual([]);

		await user.click(screen.getByRole('button', { name: 'Свернуть список аккаунтов' }));
		expect(screen.queryByRole('button', { name: /^Перетащить/ })).toBeNull();
	});

	const rows = () => within(screen.getByRole('list', { name: 'Список аккаунтов' })).getAllByRole('listitem');
	const placeRows = () =>
		rows().forEach((li, i) => {
			li.getBoundingClientRect = () => new DOMRect(0, i * 40, 200, 40);
		});

	it('потерянный захват указателя отменяет перетаскивание, следующее работает', async () => {
		const { fetch } = await setup(null);
		placeRows();
		const h = handle(1);
		await fireEvent.pointerDown(h, { pointerId: 1, button: 0, pointerType: 'mouse', clientX: 190, clientY: 20 });
		await fireEvent.pointerMove(h, { pointerId: 1, pointerType: 'mouse', clientX: 190, clientY: 70 });
		expect(rows()[0]!.style.transform).toBe('translate(0px, 50px)');
		await fireEvent.lostPointerCapture(h, { pointerId: 1, pointerType: 'mouse' });
		expect(rows().map((li) => li.style.transform)).toEqual(['', '', '']);
		expect(puts(fetch.calls)).toEqual([]);

		await fireEvent.pointerDown(h, { pointerId: 2, button: 0, pointerType: 'mouse', clientX: 190, clientY: 20 });
		await fireEvent.pointerMove(h, { pointerId: 2, pointerType: 'mouse', clientX: 190, clientY: 70 });
		await fireEvent.pointerUp(h, { pointerId: 2, pointerType: 'mouse', clientX: 190, clientY: 70 });
		expect(titles()).toEqual(['/a/2', '/a/1', '/a/3']);
		await waitFor(() => expect(puts(fetch.calls)).toEqual([{ version: 1, ids: [2, 1, 3] }]));
	});

	it('список сменился во время перетаскивания — отпускание не переставляет', async () => {
		const { fetch, reload } = await setup(null);
		placeRows();
		const h = handle(1);
		await fireEvent.pointerDown(h, { pointerId: 1, button: 0, pointerType: 'mouse', clientX: 190, clientY: 20 });
		await fireEvent.pointerMove(h, { pointerId: 1, pointerType: 'mouse', clientX: 190, clientY: 70 });
		await reload([1, 3]);
		await fireEvent.pointerUp(h, { pointerId: 1, pointerType: 'mouse', clientX: 190, clientY: 70 });
		expect(titles()).toEqual(['/a/1', '/a/3']);
		expect(rows().map((li) => li.style.transform)).toEqual(['', '']);
		await new Promise((r) => setTimeout(r, 0));
		expect(puts(fetch.calls)).toEqual([]);
	});
});
