import { describe, expect, it, vi } from 'vitest';
import { createApi } from '$lib/api/client';
import type { SearchOut } from '$lib/api/types';
import { deferred, type Deferred } from '$lib/test/deferred';
import { json } from '$lib/test/fetch';
import { SearchStore } from './search.svelte';

const sampleResult: SearchOut = {
	q: 'маска',
	where: 'db',
	accounts: [
		{ account_id: 1, name: 'Альфа', status: 'ok', reason: null, count: 2 },
		{ account_id: 2, name: 'Бета', status: 'unavailable', reason: 'engine_not_running', count: 0 }
	],
	hits: [
		{
			account_id: 1,
			chat_id: 100,
			msg_id: 10,
			date: '2026-10-17T12:00:00Z',
			outgoing: false,
			text: 'Ты надел маску 1',
			markup: null
		},
		{
			account_id: 1,
			chat_id: 100,
			msg_id: 11,
			date: '2026-10-17T12:05:00Z',
			outgoing: true,
			text: 'Снять маску',
			markup: null
		}
	]
};

function server() {
	const pending: Deferred<Response>[] = [];
	const requests: Request[] = [];
	const fetchImpl = async (input: RequestInfo | URL, init?: RequestInit) => {
		const req = input instanceof Request ? input : new Request(String(input), init);
		requests.push(req);
		const d = deferred<Response>();
		pending.push(d);
		return d.promise;
	};
	const api = createApi({ csrf: () => 'tok', refreshCsrf: async () => null, unauthorized: () => {} }, fetchImpl as typeof fetch);
	const answer = async (value: unknown = sampleResult, status = 200) => {
		const next = pending.shift();
		next?.resolve(json(value, status));
		await flush();
	};
	const query = (n: number) => Object.fromEntries(new URL(requests[n]!.url).searchParams);
	return { api, requests, answer, query, count: () => requests.length };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('SearchStore', () => {
	it('валидирует длину запроса перед отправкой и не делает запрос при ошибке', async () => {
		const s = server();
		const store = new SearchStore(s.api);
		store.q = 'а';
		await store.search();
		expect(s.count()).toBe(0);
		expect(store.validationError).toBe('Введите не менее 2 символов');
		expect(store.loading).toBe(false);
	});

	it('успешный поиск передаёт параметры в API и сохраняет результат', async () => {
		const s = server();
		const store = new SearchStore(s.api, { q: 'маска', where: 'db', accountIds: [1, 2], limit: 100 });
		const promise = store.search();
		expect(store.loading).toBe(true);
		await flush();
		expect(s.count()).toBe(1);
		expect(s.query(0)).toEqual({
			q: 'маска',
			where: 'db',
			account_ids: '1,2',
			limit: '100'
		});
		await s.answer(sampleResult);
		await promise;
		expect(store.loading).toBe(false);
		expect(store.result).toEqual(sampleResult);
		expect(store.error).toBeNull();
		expect(store.validationError).toBeNull();
	});

	it('новый поиск прерывает предыдущий незавершённый запрос (AbortController)', async () => {
		const s = server();
		const store = new SearchStore(s.api);
		store.q = 'первый запрос';
		const p1 = store.search();
		await flush();
		expect(s.count()).toBe(1);
		const firstReqSignal = s.requests[0]!.signal;
		expect(firstReqSignal.aborted).toBe(false);

		// Запускаем второй поиск до завершения первого
		store.q = 'второй запрос';
		const p2 = store.search();
		await flush();
		expect(s.count()).toBe(2);
		expect(firstReqSignal.aborted).toBe(true);

		const result2: SearchOut = { ...sampleResult, q: 'второй запрос' };
		// Отвечаем на первый запрос и на второй
		await s.answer(sampleResult);
		await s.answer(result2);
		await Promise.all([p1, p2]);

		expect(store.result?.q).toBe('второй запрос');
		expect(store.loading).toBe(false);
	});

	it('обрабатывает ошибку API и сохраняет её в store.error', async () => {
		const s = server();
		const store = new SearchStore(s.api);
		store.q = 'запрос с ошибкой';
		const promise = store.search();
		await flush();
		await s.answer({ detail: 'internal failure' }, 500);
		await promise;
		expect(store.loading).toBe(false);
		expect(store.error).not.toBeNull();
		expect(store.result).toBeNull();
	});

	it('переключает режим просмотра (группировка / плоский список)', () => {
		const s = server();
		const store = new SearchStore(s.api);
		expect(store.viewMode).toBe('grouped');
		store.setViewMode('flat');
		expect(store.viewMode).toBe('flat');
		store.setViewMode('grouped');
		expect(store.viewMode).toBe('grouped');
	});
});
