import { describe, expect, it } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import { json, mockFetch } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { UnreadCounter } from './unread.svelte';

const counter = (body: unknown) =>
	new UnreadCounter(
		createAccountApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, 1, mockFetch(() => json(body)))
	);
const note = (id: number, level: 'info' | 'warn' | 'error') =>
	({ type: 'notification', id: `e:${id}`, data: { id, level, code: 'x', text: 'x' } }) as const;

describe('Счётчик в меню', () => {
	it('с прода: 9 непрочитанных info (пауза, режим) — значка нет', async () => {
		const c = counter(fixture('notifications'));
		await c.load();
		expect(c.count).toBe(0);
	});

	it('живые warn и error прибавляются, info — нет', async () => {
		const c = counter({ items: [], unread: 3, unread_alerts: 1, next_before: null });
		await c.load();
		expect(c.count).toBe(1);
		c.onEvent(note(10, 'info'));
		c.onEvent(note(11, 'warn'));
		c.onEvent(note(12, 'error'));
		expect(c.count).toBe(3);
	});
});
