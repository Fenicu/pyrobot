import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { createApi } from '$lib/api/client';
import { dialogs } from '$lib/stores/confirm.svelte';
import type { EngineStatus, StateOut } from '$lib/api/types';
import { json, mockFetch } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import ConfirmDialog from '../ConfirmDialog.svelte';
import CharacterCard from './CharacterCard.svelte';
import ControlsCard from './ControlsCard.svelte';
import StatusHeader from './StatusHeader.svelte';
import TodayCard from './TodayCard.svelte';

const prod = fixture<StateOut>('state');
const status = fixture<EngineStatus>('engine_status');
const NOW = new Date(prod.now);

describe('Главная на снимке с прода', () => {
	it('персонаж', () => {
		render(CharacterCard, { state: prod.state, stale: prod.stale, now: NOW });
		const card = screen.getByRole('region', { name: 'Персонаж · ур. 71' });
		expect(card).toHaveTextContent('17.52M / 18.16M');
		expect(card).toHaveTextContent('$47');
		expect(card).toHaveTextContent('0 / 85');
		expect(card).toHaveTextContent('сон в отеле до 28.09 05:05');
		// Деньги устарели по политике свежести (stale с прода).
		expect(within(card).getByText('💵 деньги').parentElement).toHaveTextContent('(устарело)');
		expect(screen.getByRole('progressbar', { name: 'Опыт до уровня' })).toHaveAttribute(
			'aria-valuenow',
			'17520102'
		);
	});

	it('сегодня', () => {
		render(TodayCard, { state: prod.state, stale: prod.stale, now: NOW });
		const card = screen.getByRole('region', { name: 'Сегодня' });
		expect(card).toHaveTextContent('работа 💵 сложное · ✓ выполнено');
		expect(card).toHaveTextContent('Командное · 🔩 120/120');
		expect(card).toHaveTextContent('Битва 28.09 12:59 📯Pied Piper');
		expect(card).toHaveTextContent('доступно в 28.09 05:20');
		expect(card).toHaveTextContent('бои закончены · снова 28.09 13:10');
	});

	it('пустой снимок до первого сообщения', () => {
		render(CharacterCard, { state: {}, stale: [], now: NOW });
		expect(screen.getByText(/Снимка ещё нет/)).toBeInTheDocument();
	});

	it('шапка-статус', () => {
		render(StatusHeader, { status, error: null, live: 'open', state: prod.state, now: NOW });
		const header = screen.getByRole('region', { name: 'Статус' });
		expect(header).toHaveTextContent('LIVE');
		expect(header).toHaveTextContent('TG online');
		expect(header).toHaveTextContent('след. решение 28.09 05:05');
		expect(header).toHaveTextContent('связь есть');
	});
});

function controls(mode: 'live' | 'dry_run') {
	const fetch = mockFetch((c) => {
		if (c.method === 'GET') return json({ version: 13, values: {}, defaults: {}, schema: {} });
		if (c.method === 'PATCH')
			return json({ version: 14, values: {}, changed: {}, restart_required: [] });
		return new Response(null, { status: 204 });
	});
	const api = createApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, fetch);
	render(ConfirmDialog);
	render(ControlsCard, { api, status: { ...status, mode }, onchange: () => {} });
	return fetch;
}

describe('управление', () => {
	beforeEach(() => dialogs.answer(null));

	it('в live — только после подтверждения, с confirm_live', async () => {
		const user = userEvent.setup();
		const fetch = controls('dry_run');
		await user.click(screen.getByRole('button', { name: 'Включить live' }));
		const dialog = await screen.findByRole('dialog', { name: 'Включить LIVE?' });
		expect(dialog).toHaveTextContent('Бот начнёт реально тратить ресурсы');
		await user.click(within(dialog).getByRole('button', { name: 'Отмена' }));
		expect(fetch.calls).toEqual([]);
		await user.click(screen.getByRole('button', { name: 'Включить live' }));
		const again = await screen.findByRole('dialog', { name: 'Включить LIVE?' });
		await user.click(within(again).getByRole('button', { name: 'Включить live' }));
		await vi_wait(() => fetch.calls.some((c) => c.method === 'PATCH'));
		const patch = fetch.calls.find((c) => c.method === 'PATCH');
		expect(JSON.parse(patch?.body ?? '{}')).toEqual({
			version: 13,
			changes: { engine: { mode: 'live' } },
			confirm_live: true
		});
	});

	it('в dry_run — без окна', async () => {
		const user = userEvent.setup();
		const fetch = controls('live');
		await user.click(screen.getByRole('button', { name: 'В dry_run' }));
		expect(screen.queryByRole('dialog')).toBeNull();
		await vi_wait(() => fetch.calls.some((c) => c.method === 'PATCH'));
		const patch = fetch.calls.find((c) => c.method === 'PATCH');
		expect(JSON.parse(patch?.body ?? '{}')).toMatchObject({ confirm_live: false });
	});

	it('kill — с причиной', async () => {
		const user = userEvent.setup();
		const fetch = controls('live');
		await user.click(screen.getByRole('button', { name: 'Kill' }));
		await user.type(await screen.findByLabelText('Причина'), 'проверка{Enter}');
		await vi_wait(() => fetch.calls.length > 0);
		expect(fetch.calls[0]).toMatchObject({ method: 'POST', url: '/api/v1/engine/kill' });
		expect(JSON.parse(fetch.calls[0]?.body ?? '')).toEqual({ reason: 'проверка' });
	});
});

async function vi_wait(cond: () => boolean): Promise<void> {
	for (let i = 0; i < 50 && !cond(); i++) await new Promise((r) => setTimeout(r, 5));
	expect(cond()).toBe(true);
}
