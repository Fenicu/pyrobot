import { cleanup, render, screen, waitFor } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import type { EngineStatus } from '$lib/api/types';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import GameChatBanner from './GameChatBanner.svelte';

const hooks = { csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} };

const status = (member: boolean | null): EngineStatus => ({
	...fixture<EngineStatus>('engine_status'),
	game_chat_member: member
});

function banner(
	member: boolean | null,
	handler: (c: Call) => Response | Promise<Response> = () => json({ status: 'joined', game_chat_member: true })
) {
	const fetch = mockFetch(handler);
	const onchange = vi.fn();
	const view = render(GameChatBanner, { status: status(member), api: createAccountApi(hooks, 7, fetch), onchange });
	return { fetch, onchange, view, user: userEvent.setup() };
}

const JOIN = { name: 'Вступить в общий чат игры' };

describe('плашка «аккаунт не в общем чате игры»', () => {
	it('состоит или ещё неизвестно — плашки нет', () => {
		banner(true);
		expect(screen.queryByRole('status')).toBeNull();
		cleanup();
		banner(null);
		expect(screen.queryByRole('status')).toBeNull();
	});

	it('не состоит — текст и кнопка', () => {
		banner(false);
		expect(screen.getByRole('status')).toHaveTextContent('Аккаунт не состоит в общем чате игры @startupwarschat');
		expect(screen.getByRole('button', JOIN)).toBeEnabled();
	});

	it('на телефоне: колонка, текст и кнопка на всю ширину; с sm — ряд', () => {
		banner(false);
		const section = screen.getByRole('status');
		expect(section).toHaveClass('flex-col', 'sm:flex-row');
		expect(section).not.toHaveClass('flex-wrap');
		const text = screen.getByText(/Аккаунт не состоит/);
		expect(text).toHaveClass('w-full');
		expect(text).not.toHaveClass('flex-1');
		expect(screen.getByRole('button', JOIN)).toHaveClass('w-full', 'sm:w-auto', 'sm:shrink-0');
	});

	it('заявка отправлена и ошибка — на всю ширину', async () => {
		const { user } = banner(false, () => json({ detail: 'join_declined' }, 502));
		await user.click(screen.getByRole('button', JOIN));
		expect(await screen.findByRole('alert')).toHaveClass('w-full');
	});

	it('вступил: POST на путь аккаунта, плашка скрыта, статус перечитывается; новый статус решает заново', async () => {
		const { fetch, onchange, user, view } = banner(false);
		await user.click(screen.getByRole('button', JOIN));
		await waitFor(() => expect(onchange).toHaveBeenCalledOnce());
		expect([fetch.calls[0]!.method, fetch.calls[0]!.url]).toEqual([
			'POST',
			'/api/v1/accounts/7/tg/game-chat/join'
		]);
		expect(screen.queryByRole('status')).toBeNull();

		await view.rerender({ status: status(false) });
		expect(screen.getByRole('status')).toBeInTheDocument();
	});

	it('уже состоял — тоже скрыта', async () => {
		const { onchange, user } = banner(false, () => json({ status: 'already_member', game_chat_member: true }));
		await user.click(screen.getByRole('button', JOIN));
		await waitFor(() => expect(onchange).toHaveBeenCalledOnce());
		expect(screen.queryByRole('status')).toBeNull();
	});

	it('заявка отправлена — текст ожидания, кнопки нет', async () => {
		const { onchange, user } = banner(false, () => json({ status: 'request_sent', game_chat_member: false }));
		await user.click(screen.getByRole('button', JOIN));
		expect(await screen.findByText('Заявка отправлена, ждёт одобрения в чате')).toBeInTheDocument();
		expect(screen.queryByRole('button', JOIN)).toBeNull();
		expect(onchange).not.toHaveBeenCalled();
	});

	it('во время запроса кнопка выключена', async () => {
		let release: (r: Response) => void = () => {};
		const { user } = banner(false, () => new Promise<Response>((r) => (release = r)));
		await user.click(screen.getByRole('button', JOIN));
		const button = screen.getByRole('button', { name: 'Вступаю…' });
		expect(button).toBeDisabled();
		release(json({ status: 'request_sent', game_chat_member: false }));
		await screen.findByText('Заявка отправлена, ждёт одобрения в чате');
	});

	it.each([
		[409, 'game_chat_mismatch', {}, 'Чат @startupwarschat не совпадает с чатом SWINFO из настроек — вступление отменено'],
		[409, 'tg_not_online', {}, 'Telegram не в онлайне — сначала подключите аккаунт'],
		[429, 'flood_wait', { 'Retry-After': '42' }, 'Telegram просит подождать 42 с'],
		[429, 'flood_wait', {}, 'Telegram просит подождать'],
		[502, 'join_declined', {}, 'Заявку на вступление отклонили'],
		[502, 'CHANNELS_TOO_MUCH', {}, 'Telegram отказал: CHANNELS_TOO_MUCH'],
		[503, 'engine not running', {}, 'Движок недоступен']
	])('ошибка %i %s — текст для человека, кнопка остаётся', async (code, detail, headers, text) => {
		const { onchange, user } = banner(false, () => json({ detail }, code, headers));
		await user.click(screen.getByRole('button', JOIN));
		expect((await screen.findByRole('alert')).textContent?.trim()).toBe(text);
		expect(screen.getByRole('button', JOIN)).toBeEnabled();
		expect(onchange).not.toHaveBeenCalled();
	});

	it('текст плашки без лишних переносов и отступов', () => {
		banner(false);
		const p = screen.getByText(/Аккаунт не состоит/);
		expect(p.textContent).not.toMatch(/[\n\t]/);
		expect(p.textContent).toContain('цены акций.');
	});
});
