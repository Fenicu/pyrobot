import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import { json, mockFetch } from '$lib/test/fetch';
import TangerinePartner from './TangerinePartner.svelte';

const hooks = { csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} };

describe('TangerinePartner', () => {
	it('читает адресата и перечитывает при смене refresh', async () => {
		let name = 'twink';
		const fetch = mockFetch(() =>
			json({
				reply_to: 7,
				sender: { tg_user_id: 42, name: 'Анна', username: null },
				account: { id: 2, name },
				status: 'ok'
			})
		);
		const { rerender } = render(TangerinePartner, { api: createAccountApi(hooks, 1, fetch), refresh: 7 });
		const link = await screen.findByRole('link', { name: '🍊 обмен с twink' });
		expect(link.getAttribute('href')).toBe('/a/2');
		expect(fetch.calls.map((c) => `${c.method} ${c.url}`)).toEqual(['GET /api/v1/accounts/1/tangerine/partner']);
		name = 'main';
		await rerender({ refresh: 8 });
		expect(await screen.findByRole('link', { name: '🍊 обмен с main' })).toBeTruthy();
		expect(fetch.calls).toHaveLength(2);
	});

	it('ошибка запроса — ничего не показывает', async () => {
		const fetch = mockFetch(() => json({ detail: 'boom' }, 500));
		const { container } = render(TangerinePartner, { api: createAccountApi(hooks, 1, fetch), refresh: 1 });
		await Promise.resolve();
		await new Promise((r) => setTimeout(r, 0));
		expect(fetch.calls).toHaveLength(1);
		expect(container.textContent?.trim()).toBe('');
	});
});
