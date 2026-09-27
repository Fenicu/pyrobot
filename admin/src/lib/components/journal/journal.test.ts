import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { createApi } from '$lib/api/client';
import type { JournalPage } from '$lib/api/types';
import { JournalFeed } from '$lib/stores/journal.svelte';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import JournalView from './JournalView.svelte';

const page = fixture<JournalPage>('journal_page');

async function view(handler: (c: Call) => Response | undefined = () => undefined) {
	const fetch = mockFetch((c) => {
		const custom = handler(c);
		if (custom) return custom;
		if (c.url.startsWith('/api/v1/journal')) return json(page);
		if (c.url === '/api/v1/decisions/344') return json(fixture('decision_detail'));
		if (c.url === '/api/v1/actions/474') return json({ ...fixture<object>('action_detail'), idempotency_key: null, scenario_run_id: 34 });
		if (c.url === '/api/v1/scenario-runs/34')
			return json({
				id: 34, decision_id: 343, scenario: 'sleep', params: { hours: 7 }, started_at: '2026-09-27T19:05:03Z',
				finished_at: '2026-09-27T19:05:12Z', status: 'done', reason: 'fell_asleep', requested_by: null,
				metro_run_id: null,
				actions: [{ ...fixture<object>('action_detail'), idempotency_key: null, scenario_run_id: 34 }]
			});
		return json({ detail: 'Not Found' }, 404);
	});
	const api = createApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, fetch);
	const feed = new JournalFeed(api);
	await feed.reload();
	const confirmer = vi.fn(async () => true);
	render(JournalView, { api, feed, confirmer, now: new Date('2026-09-27T20:27:51Z') });
	return { fetch, feed, confirmer };
}

describe('Журнал на странице с прода', () => {
	it('лента: типы строк и текст — только текстом', async () => {
		await view();
		const list = screen.getByRole('region', { name: 'Лента' });
		const rows = within(list).getAllByRole('button', { pressed: false });
		expect(rows.length).toBeGreaterThanOrEqual(60);
		expect(list).toHaveTextContent('22:47:42 решение ожидание → busy до 28.09 05:05');
		expect(list).toHaveTextContent('сценарий · sleep_Hotel → fell_asleep');
		expect(list).toHaveTextContent('Ты отправился спать красиво в отель на 7 часов за 213 💵');
	});

	it('разбор решения — шторкой', async () => {
		const user = userEvent.setup();
		await view();
		await user.click(screen.getAllByRole('button', { name: /ожидание → busy/ })[0]!);
		const sheet = await screen.findByRole('dialog', { name: 'Решение' });
		expect(await within(sheet).findByText(/Решение #344/)).toBeInTheDocument();
		expect(sheet).toHaveTextContent('Нет (ожидание без выбора)');
		await user.keyboard('{Escape}');
		expect(screen.queryByRole('dialog')).toBeNull();
	});

	it('разбор действия с шагами его запуска', async () => {
		const user = userEvent.setup();
		await view();
		await user.click(screen.getByRole('button', { name: /sleep_Hotel → fell_asleep/ }));
		const sheet = await screen.findByRole('dialog', { name: 'Действие' });
		expect(await within(sheet).findByText(/Действие #474/)).toBeInTheDocument();
		const steps = await within(sheet).findByRole('list', { name: 'Шаги запуска #34' });
		expect(steps).toHaveTextContent('sleep_Hotel');
		expect(sheet).toHaveTextContent('Запуск #34 · sleep');
	});

	it('клик по кнопке сообщения: подтверждение и тот же ключ', async () => {
		const user = userEvent.setup();
		const confirm = {
			code: 'confirm_required', reason: 'missing', confirm_token: 'tok', expires_at: '2026-09-27T20:00:00Z',
			state_version: 1, command_class: 'risky'
		};
		const { fetch, confirmer } = await view((c) => {
			if (c.url !== '/api/v1/commands/click') return undefined;
			return JSON.parse(c.body).confirm_token
				? json({ action_id: 480, status: 'confirmed', reason: 'fell_asleep', answer: null })
				: json({ detail: confirm }, 409);
		});
		const row = screen.getAllByRole('button').find((b) => b.textContent?.includes('Все мы рано или поздно'));
		await user.click(row!);
		const sheet = await screen.findByRole('dialog', { name: 'Сообщение' });
		expect(sheet).toHaveTextContent('Где собираешься спать?');
		await user.click(within(sheet).getByRole('button', { name: 'В отеле - 213 💵' }));
		await vi.waitFor(() => expect(fetch.calls.filter((c) => c.url === '/api/v1/commands/click')).toHaveLength(2));
		const [first, second] = fetch.calls.filter((c) => c.url === '/api/v1/commands/click').map((c) => JSON.parse(c.body));
		expect(first).toMatchObject({ chat_id: 227859379, message_id: 3626304, revision: 1790535904, callback_data: 'sleep_Hotel' });
		expect(second).toEqual({ ...first, confirm_token: 'tok' });
		expect(confirmer).toHaveBeenCalledWith(confirm, 'кнопка «В отеле - 213 💵»');
	});

	it('сообщение изменилось — лента перечитывается', async () => {
		const user = userEvent.setup();
		const { fetch } = await view((c) =>
			c.url === '/api/v1/commands/click'
				? json({ action_id: 481, status: 'rejected', reason: 'stale_revision', answer: null })
				: undefined
		);
		const row = screen.getAllByRole('button').find((b) => b.textContent?.includes('Все мы рано или поздно'));
		await user.click(row!);
		const sheet = await screen.findByRole('dialog', { name: 'Сообщение' });
		const before = fetch.calls.filter((c) => c.url.startsWith('/api/v1/journal')).length;
		await user.click(within(sheet).getByRole('button', { name: 'Под мостом - 0 💵' }));
		await vi.waitFor(() =>
			expect(fetch.calls.filter((c) => c.url.startsWith('/api/v1/journal')).length).toBe(before + 1)
		);
	});
});
