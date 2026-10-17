import { cleanup, render, screen, waitFor, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { BattleHq } from '$lib/api/types';
import type { Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import Page from './+page.svelte';

const h = vi.hoisted(() => ({
	hq: null as unknown,
	calls: [] as Call[],
	put: null as ((call: Call) => unknown) | null,
	putStatus: 200,
	getStatus: 200
}));

vi.mock('$lib/app.svelte', async () => {
	const { createApi } = await import('$lib/api/client');
	const { json, mockFetch } = await import('$lib/test/fetch');
	const hooks = { csrf: () => 'tok', refreshCsrf: async () => null, unauthorized: () => {} };
	const fetch = mockFetch((call) => {
		h.calls.push(call);
		if (call.method === 'PUT' && h.put) return json(h.put(call), h.putStatus);
		if (call.url === '/api/v1/overview/battle') return json(h.hq, h.getStatus);
		return json({ detail: 'not found' }, 404);
	});
	return { api: createApi(hooks, fetch) };
});

const base = fixture<BattleHq>('overview_battle');

function show(hq: BattleHq) {
	h.hq = hq;
	return render(Page);
}

afterEach(() => {
	cleanup();
	h.calls.length = 0;
	h.put = null;
	h.putStatus = 200;
	h.getStatus = 200;
});

describe('экран «Штаб битвы»', () => {
	it('две карточки: ближайшая и следующая, у каждой своя сводка', async () => {
		show(base);
		expect(await screen.findByRole('region', { name: /^Ближайшая битва 13:00/ })).toBeInTheDocument();
		expect(screen.getByRole('region', { name: 'Следующая 22:00' })).toBeInTheDocument();
		expect(screen.getAllByRole('group', { name: '☂️Umbrella' })).toHaveLength(1);
		expect(screen.getByText(/Не входят в штаб/)).toHaveTextContent('#5');
	});

	it('«Сохранить» шлёт точное тело на эту битву и показывает итог', async () => {
		const user = userEvent.setup();
		h.put = () => ({
			...base.battles[0],
			orders: { all: '🤖Hooli', corporations: { bmesa: '☂️Umbrella' } },
			woken: [1, 2, 3],
			not_woken: []
		});
		show(base);
		const card = within(await screen.findByRole('region', { name: /^Ближайшая битва/ }));
		await user.selectOptions(card.getByLabelText('📯Pied Piper'), '🛡Защита');
		await user.click(card.getByRole('button', { name: 'Сохранить' }));
		const put = await waitFor(() => {
			const call = h.calls.find((c) => c.method === 'PUT');
			expect(call).toBeDefined();
			return call!;
		});
		expect(decodeURIComponent(put.url)).toBe('/api/v1/overview/battle/2026-10-17T10:00:00+00:00/orders');
		expect(JSON.parse(put.body)).toEqual({
			all: '🤖Hooli',
			corporations: { bmesa: '☂️Umbrella', piper: '🛡Защита' }
		});
		expect(await card.findByRole('status')).toHaveTextContent('Сохранено. Движки разбужены: 3');
	});

	it('422 target_is_own_company — текст ошибки у карточки', async () => {
		const user = userEvent.setup();
		h.putStatus = 422;
		h.put = () => ({
			detail: [{ loc: ['body', 'corporations', 'bmesa'], msg: 'x', type: 'target_is_own_company' }]
		});
		show(base);
		const card = within(await screen.findByRole('region', { name: /^Ближайшая битва/ }));
		await user.selectOptions(card.getByLabelText('Всем'), '');
		await user.click(card.getByRole('button', { name: 'Сохранить' }));
		expect(await card.findByRole('alert')).toHaveTextContent('Корпорация «☣️Black Mesa» не может атаковать саму себя');
	});

	it('за минуту до битвы форма закрыта', async () => {
		const late = structuredClone(base);
		late.battles[0] = { ...late.battles[0]!, editable: false, note: 'too_late', in_s: 20 };
		show(late);
		const card = within(await screen.findByRole('region', { name: /^Ближайшая битва/ }));
		expect(card.getByLabelText('Всем')).toBeDisabled();
		expect(card.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
		expect(card.getByText('до битвы меньше минуты: приказ уже не применится')).toBeInTheDocument();
		// Следующая битва по-прежнему открыта.
		expect(within(screen.getByRole('region', { name: 'Следующая 22:00' })).getByLabelText('Всем')).toBeEnabled();
	});

	it('нет аккаунтов — ссылка на список', async () => {
		const none: BattleHq = {
			...base,
			companies: [],
			skipped: [],
			battles: base.battles.map((b) => ({ ...b, accounts: [], summary: [], orders: { all: null, corporations: {} } }))
		};
		show(none);
		const link = await screen.findByRole('link', { name: 'Открыть аккаунты' });
		expect(link).toHaveAttribute('href', '/accounts');
		expect(screen.queryByRole('region', { name: /битва/ })).toBeNull();
	});

	it('ошибка чтения без данных', async () => {
		h.getStatus = 500;
		show({ detail: 'boom' } as unknown as BattleHq);
		expect(await screen.findByRole('alert')).toHaveTextContent('Штаб недоступен');
	});
});
