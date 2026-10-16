import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { ManualControl } from './control.svelte';

const NOW = new Date('2026-10-17T10:00:00Z');
const UNTIL = '2026-10-17T10:30:00Z';

function setup(reply: (c: Call) => Response = (c) => json({ until: JSON.parse(c.body).on ? UNTIL : null })) {
	const fetch = mockFetch(reply);
	const api = createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
	return { fetch, control: new ManualControl(api) };
}

describe('ручное управление', () => {
	beforeEach(() => vi.useFakeTimers({ now: NOW, toFake: ['setTimeout', 'clearTimeout', 'Date'] }));
	afterEach(() => vi.useRealTimers());

	it('взять и вернуть: POST {on} и срок из ответа', async () => {
		const { fetch, control } = setup();
		await control.take();
		expect(control.until).toBe(UNTIL);
		await control.release();
		expect(control.until).toBeNull();
		expect(fetch.calls.map((c) => [c.method, c.url, JSON.parse(c.body)])).toEqual([
			['POST', '/api/v1/accounts/1/engine/manual-control', { on: true }],
			['POST', '/api/v1/accounts/1/engine/manual-control', { on: false }]
		]);
	});

	it('срок прошёл — управление у бота без кадра потока', async () => {
		const { control } = setup();
		control.sync(UNTIL);
		expect(control.until).toBe(UNTIL);
		await vi.advanceTimersByTimeAsync(30 * 60_000 - 1);
		expect(control.until).toBe(UNTIL);
		await vi.advanceTimersByTimeAsync(1);
		expect(control.until).toBeNull();
		control.sync('2026-10-17T09:00:00Z');
		expect(control.until).toBeNull();
	});

	it('кадр manual_control: продление и возврат', () => {
		const { control } = setup();
		control.onEvent({ type: 'manual_control', id: 'e:1', data: { until: UNTIL } });
		expect(control.until).toBe(UNTIL);
		control.onEvent({ type: 'manual_control', id: 'e:2', data: { until: null } });
		expect(control.until).toBeNull();
	});

	it('ошибка — текстом, срок прежний', async () => {
		const { control } = setup(() => json({ detail: 'engine not running' }, 503));
		await control.take();
		expect(control.until).toBeNull();
		expect(control.error).not.toBe('');
		expect(control.busy).toBe(false);
	});
});
