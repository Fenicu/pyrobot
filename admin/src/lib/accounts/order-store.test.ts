import { afterEach, describe, expect, it } from 'vitest';
import { createApi } from '$lib/api/client';
import type { AccountOut } from '$lib/api/types';
import { AccountsStore } from '$lib/stores/accounts.svelte';
import { toasts } from '$lib/stores/toasts.svelte';
import { deferred, flush } from '$lib/test/deferred';
import { json, mockFetch, type Handler } from '$lib/test/fetch';
import { AccountOrderStore, SAVE_FAILED } from './order.svelte';

const hooks = { csrf: () => 'tok', refreshCsrf: async () => 'tok', unauthorized: () => {} };
const URL_ = '/api/v1/me/ui/account-order';

function setup(handler: Handler) {
	const fetch = mockFetch(handler);
	return { store: new AccountOrderStore(createApi(hooks, fetch)), fetch };
}

afterEach(() => {
	toasts.items = [];
});

describe('AccountOrderStore: загрузка', () => {
	it('сохранённого нет — null', async () => {
		const { store, fetch } = setup(() => json({ order: null }));
		await store.load();
		expect(fetch.calls.map((c) => `${c.method} ${c.url}`)).toEqual([`GET ${URL_}`]);
		expect(store.ids).toBeNull();
	});

	it('сохранённый — его id', async () => {
		const { store } = setup(() => json({ order: { version: 1, ids: [3, 1] } }));
		await store.load();
		expect(store.ids).toEqual([3, 1]);
	});

	it('мусор от сервера — null', async () => {
		const { store } = setup(() => json({ order: { version: 1, ids: 'x' } }));
		await store.load();
		expect(store.ids).toBeNull();
	});

	it('ошибка чтения — порядок по умолчанию, без тоста', async () => {
		const { store } = setup(() => json({ detail: 'boom' }, 500));
		await store.load();
		expect(store.ids).toBeNull();
		expect(toasts.items).toEqual([]);
	});

	it('start читает один раз до stop; stop забывает порядок', async () => {
		const { store, fetch } = setup(() => json({ order: { version: 1, ids: [2, 1] } }));
		store.start();
		store.start();
		await flush();
		expect(fetch.calls).toHaveLength(1);
		expect(store.ids).toEqual([2, 1]);
		store.stop();
		expect(store.ids).toBeNull();
		store.start();
		await flush();
		expect(fetch.calls).toHaveLength(2);
	});
});

describe('AccountOrderStore: сохранение', () => {
	it('сразу на экране, PUT с новым порядком', async () => {
		const { store, fetch } = setup((c) => (c.method === 'PUT' ? json(null, 204) : json({ order: null })));
		const done = store.save([2, 1]);
		expect(store.ids).toEqual([2, 1]);
		expect(await done).toBe(true);
		const put = fetch.calls.find((c) => c.method === 'PUT')!;
		expect(put.url).toBe(URL_);
		expect(JSON.parse(put.body)).toEqual({ version: 1, ids: [2, 1] });
		expect(put.headers.get('X-CSRF-Token')).toBe('tok');
		expect(toasts.items).toEqual([]);
	});

	it('ошибка записи — прежний порядок и тост', async () => {
		const { store } = setup((c) => (c.method === 'PUT' ? json({ detail: 'boom' }, 500) : json({ order: { version: 1, ids: [1, 2] } })));
		await store.load();
		expect(await store.save([2, 1])).toBe(false);
		expect(store.ids).toEqual([1, 2]);
		expect(toasts.items.map((t) => [t.kind, t.text])).toEqual([['error', SAVE_FAILED]]);
	});

	it('ответ чтения после записи не перетирает новый порядок', async () => {
		const read = deferred<Response>();
		const { store } = setup((c) => (c.method === 'PUT' ? json(null, 204) : read.promise));
		const loading = store.load();
		await store.save([2, 1]);
		read.resolve(json({ order: { version: 1, ids: [1, 2] } }));
		await loading;
		expect(store.ids).toEqual([2, 1]);
	});

	it('упавшая запись не откатывает более новую', async () => {
		const first = deferred<Response>();
		let puts = 0;
		const { store } = setup(() => (++puts === 1 ? first.promise : json(null, 204)));
		const a = store.save([2, 1]);
		await store.save([3, 2, 1]);
		first.resolve(json({ detail: 'boom' }, 500));
		expect(await a).toBe(false);
		expect(store.ids).toEqual([3, 2, 1]);
	});
});

describe('AccountsStore с порядком', () => {
	const account = (id: number): AccountOut => ({
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
		alert: null
	});

	it('list — по сохранённому порядку, reorder сохраняет', async () => {
		const fetch = mockFetch((c) => {
			if (c.url === '/api/v1/accounts') return json([account(1), account(2), account(3)]);
			if (c.method === 'PUT') return json(null, 204);
			return json({ order: { version: 1, ids: [3, 1] } });
		});
		const api = createApi(hooks, fetch);
		const order = new AccountOrderStore(api);
		const store = new AccountsStore(api, undefined, order);
		await store.load();
		expect(store.list?.map((a) => a.id)).toEqual([1, 2, 3]);
		await order.load();
		expect(store.list?.map((a) => a.id)).toEqual([3, 1, 2]);
		expect(await store.reorder([2, 3, 1])).toBe(true);
		expect(store.list?.map((a) => a.id)).toEqual([2, 3, 1]);
		expect(JSON.parse(fetch.calls.at(-1)!.body)).toEqual({ version: 1, ids: [2, 3, 1] });
	});
});
