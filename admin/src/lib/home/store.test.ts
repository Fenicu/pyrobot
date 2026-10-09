import { describe, expect, it } from 'vitest';
import { createApi } from '$lib/api/client';
import { json, mockFetch, type Handler } from '$lib/test/fetch';
import { DEFAULT_LAYOUT, DEFAULT_SIZE, type HomeLayout } from './layout';
import { HomeLayoutStore } from './store.svelte';

const hooks = { csrf: () => 'tok', refreshCsrf: async () => 'tok', unauthorized: () => {} };
const URL_ = '/api/v1/me/ui/home-layout';

function setup(handler: Handler) {
	const fetch = mockFetch(handler);
	return { store: new HomeLayoutStore(createApi(hooks, fetch)), fetch };
}

const clone = (l: HomeLayout): HomeLayout => JSON.parse(JSON.stringify(l));
const saved = (): HomeLayout => {
	const l = clone(DEFAULT_LAYOUT);
	l.items = l.items.filter((i) => i.id !== 'artifact');
	l.items[0] = { id: 'now', x: 0, y: 0, w: 5, h: 4 };
	l.hidden = ['artifact'];
	return l;
};

describe('HomeLayoutStore: загрузка', () => {
	it('до загрузки — раскладка по умолчанию, не в режиме правки', async () => {
		const { store } = setup(() => json({ layout: null }));
		expect(store.layout).toEqual(DEFAULT_LAYOUT);
		expect(store.draft).toBeNull();
		expect(store.editing).toBe(false);
		expect(store.dirty).toBe(false);
	});

	it('сохранённой нет (null) — по умолчанию', async () => {
		const { store, fetch } = setup(() => json({ layout: null }));
		await store.load();
		expect(fetch.calls.map((c) => `${c.method} ${c.url}`)).toEqual([`GET ${URL_}`]);
		expect(store.layout).toEqual(DEFAULT_LAYOUT);
		expect(store.error).toBeNull();
	});

	it('сохранённая — нормализованная', async () => {
		const { store } = setup(() => json({ layout: { ...saved(), items: [...saved().items, { id: 'x', x: 0, y: 0, w: 1, h: 1 }] } }));
		await store.load();
		expect(store.layout).toEqual(saved());
	});

	it('мусор от сервера — по умолчанию', async () => {
		const { store } = setup(() => json({ layout: { version: 7, items: 'no' } }));
		await store.load();
		expect(store.layout).toEqual(DEFAULT_LAYOUT);
	});

	it('ошибка чтения — остаётся прежняя раскладка, ошибка запомнена', async () => {
		const { store } = setup(() => json({ detail: 'boom' }, 500));
		await store.load();
		expect(store.layout).toEqual(DEFAULT_LAYOUT);
		expect(store.error).toMatchObject({ status: 500 });
	});

	it('start читает один раз до stop; stop возвращает умолчание', async () => {
		const { store, fetch } = setup(() => json({ layout: saved() }));
		store.start();
		store.start();
		await expect.poll(() => store.layout).toEqual(saved());
		expect(fetch.calls).toHaveLength(1);
		await store.begin();
		store.stop();
		expect(store.layout).toEqual(DEFAULT_LAYOUT);
		expect(store.draft).toBeNull();
		store.start();
		await expect.poll(() => fetch.calls.length).toBe(2);
	});
});

describe('HomeLayoutStore: правка', () => {
	it('begin — черновик-копия, без изменений не dirty; cancel — выход без изменений', async () => {
		const { store } = setup(() => json({ layout: saved() }));
		await store.load();
		await store.begin();
		expect(store.editing).toBe(true);
		expect(store.draft).toEqual(saved());
		expect(store.draft).not.toBe(store.layout);
		expect(store.dirty).toBe(false);
		store.hide('now');
		expect(store.dirty).toBe(true);
		store.cancel();
		expect(store.editing).toBe(false);
		expect(store.layout).toEqual(saved());
	});

	it('hide убирает блок в скрытые, restore возвращает его в первое свободное место с размером по умолчанию', async () => {
		const { store } = setup(() => json({ layout: null }));
		await store.begin();
		store.hide('character');
		expect(store.draft!.items.map((i) => i.id)).not.toContain('character');
		expect(store.draft!.hidden).toEqual(['character']);
		store.hide('character');
		expect(store.draft!.hidden).toEqual(['character']);
		store.restore('character');
		expect(store.draft!.hidden).toEqual([]);
		// Место персонажа (x 5, y 0, 4×10) освободилось — туда он и встаёт.
		expect(store.draft!.items.find((i) => i.id === 'character')).toEqual({
			id: 'character',
			x: 5,
			y: 0,
			...DEFAULT_SIZE.character
		});
		expect(store.layout).toEqual(DEFAULT_LAYOUT);
	});

	it('restore в занятой сетке — ниже, где помещается', async () => {
		const { store } = setup(() => json({ layout: null }));
		await store.begin();
		store.hide('artifact');
		store.update({ ...store.draft!, items: store.draft!.items.map((i) => (i.id === 'daily' ? { ...i, h: 12 } : i)) });
		store.restore('artifact');
		expect(store.draft!.items.find((i) => i.id === 'artifact')).toEqual({ id: 'artifact', x: 0, y: 18, w: 3, h: 5 });
	});

	it('reset — раскладка по умолчанию в черновике, сохранённая не трогается', async () => {
		const { store } = setup(() => json({ layout: saved() }));
		await store.load();
		await store.begin();
		store.reset();
		expect(store.draft).toEqual(DEFAULT_LAYOUT);
		expect(store.dirty).toBe(true);
		expect(store.layout).toEqual(saved());
	});

	it('update вне режима правки — только то, что видно на экране, без записи', async () => {
		const { store, fetch } = setup(() => json({ layout: null }));
		const fixed = clone(DEFAULT_LAYOUT);
		fixed.items[0]!.y = 1;
		store.update(fixed);
		expect(store.layout).toEqual(fixed);
		expect(store.editing).toBe(false);
		expect(fetch.calls).toHaveLength(0);
	});

	it('операции правки вне режима правки ничего не делают', async () => {
		const { store } = setup(() => json({ layout: null }));
		store.hide('now');
		store.restore('now');
		store.reset();
		expect(store.draft).toBeNull();
		expect(store.layout).toEqual(DEFAULT_LAYOUT);
	});
});

describe('HomeLayoutStore: сохранение', () => {
	it('успех — PUT черновика с CSRF, раскладка = черновик, режим правки закрыт', async () => {
		const { store, fetch } = setup((c) => (c.method === 'PUT' ? json(null, 204) : json({ layout: null })));
		await store.begin();
		store.hide('artifact');
		const draft = clone(store.draft!);
		const pending = store.save();
		expect(store.saving).toBe(true);
		expect(await pending).toBe(true);
		const put = fetch.calls.find((c) => c.method === 'PUT')!;
		expect(put.url).toBe(URL_);
		expect(put.headers.get('X-CSRF-Token')).toBe('tok');
		expect(JSON.parse(put.body)).toEqual(draft);
		expect(store.layout).toEqual(draft);
		expect(store.draft).toBeNull();
		expect(store.saving).toBe(false);
		expect(store.error).toBeNull();
	});

	it('ошибка — черновик и режим правки остаются, ошибка запомнена', async () => {
		const { store } = setup((c) => (c.method === 'PUT' ? json({ detail: 'store_failed' }, 503) : json({ layout: null })));
		await store.begin();
		store.hide('artifact');
		const draft = clone(store.draft!);
		expect(await store.save()).toBe(false);
		expect(store.draft).toEqual(draft);
		expect(store.editing).toBe(true);
		expect(store.layout).toEqual(DEFAULT_LAYOUT);
		expect(store.error).toMatchObject({ kind: 'store_failed' });
		expect(store.saving).toBe(false);
	});

	it('без изменений — выход из режима правки без запроса', async () => {
		const { store, fetch } = setup((c) => (c.method === 'PUT' ? json(null, 204) : json({ layout: null })));
		await store.begin();
		expect(await store.save()).toBe(true);
		expect(store.editing).toBe(false);
		expect(fetch.calls.filter((c) => c.method === 'PUT')).toHaveLength(0);
	});
});

describe('HomeLayoutStore: «Настроить» без прочитанной раскладки', () => {
	it('чтение упало — begin читает заново и открывает правку на серверной раскладке', async () => {
		let fail = true;
		const { store, fetch } = setup(() => (fail ? json({ detail: 'boom' }, 500) : json({ layout: saved() })));
		await store.load();
		expect(store.error).toMatchObject({ status: 500 });
		fail = false;
		expect(await store.begin()).toBe(true);
		expect(fetch.calls.filter((c) => c.method === 'GET')).toHaveLength(2);
		expect(store.editing).toBe(true);
		expect(store.draft).toEqual(saved());
		expect(store.error).toBeNull();
	});

	it('повторное чтение тоже упало — режим правки не открывается, ошибка остаётся', async () => {
		const { store } = setup(() => json({ detail: 'boom' }, 500));
		await store.load();
		expect(await store.begin()).toBe(false);
		expect(store.editing).toBe(false);
		expect(store.draft).toBeNull();
		expect(store.error).toMatchObject({ status: 500 });
	});

	it('раскладка прочитана — begin не ходит на сервер', async () => {
		const { store, fetch } = setup(() => json({ layout: saved() }));
		await store.load();
		expect(await store.begin()).toBe(true);
		expect(fetch.calls).toHaveLength(1);
	});

	it('после stop прочитанность сбрасывается', async () => {
		const { store, fetch } = setup(() => json({ layout: saved() }));
		await store.load();
		store.stop();
		await store.begin();
		expect(fetch.calls.filter((c) => c.method === 'GET')).toHaveLength(2);
	});
});

describe('HomeLayoutStore: выход во время записи', () => {
	it('PUT, завершившийся после stop, раскладку и ошибку не меняет', async () => {
		let release: () => void = () => {};
		const gate = new Promise<void>((r) => (release = r));
		const { store } = setup(async (c) => {
			if (c.method !== 'PUT') return json({ layout: null });
			await gate;
			return json(null, 204);
		});
		await store.begin();
		store.hide('artifact');
		const pending = store.save();
		store.stop();
		release();
		expect(await pending).toBe(false);
		expect(store.layout).toEqual(DEFAULT_LAYOUT);
		expect(store.draft).toBeNull();
		expect(store.saving).toBe(false);
		expect(store.error).toBeNull();
	});

	it('ошибка PUT после stop не попадает в ошибку нового входа', async () => {
		let release: () => void = () => {};
		const gate = new Promise<void>((r) => (release = r));
		const { store } = setup(async (c) => {
			if (c.method !== 'PUT') return json({ layout: null });
			await gate;
			return json({ detail: 'store_failed' }, 503);
		});
		await store.begin();
		store.hide('artifact');
		const pending = store.save();
		store.stop();
		release();
		expect(await pending).toBe(false);
		expect(store.error).toBeNull();
	});
});
