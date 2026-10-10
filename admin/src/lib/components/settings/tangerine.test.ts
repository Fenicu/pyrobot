import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import type { AccountOut, SettingsOut } from '$lib/api/types';
import { SettingsEditor } from '$lib/settings/editor.svelte';
import { dialogs } from '$lib/stores/confirm.svelte';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import ConfirmDialog from '../ConfirmDialog.svelte';
import SettingsView from './SettingsView.svelte';
import TangerineExchange from './TangerineExchange.svelte';

vi.mock('$app/state', async () => ({ page: (await import('$lib/test/page.svelte')).page }));
vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => {}) }));

const hooks = { csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} };

const account = (id: number, name: string, over: Partial<AccountOut> = {}): AccountOut => ({
	id,
	name,
	status: 'enabled',
	status_reason: null,
	blocked: false,
	blocked_reason: null,
	tg: { user_id: 100 + id, online: true },
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
	alert: null,
	...over
});

const ACCOUNTS = [
	account(1, 'main'),
	account(2, 'twink'),
	account(3, 'old', { tg: { user_id: 103, online: false } })
];

function block(reply: (c: Call) => Response | Promise<Response> = () => json({ chat_id: -1001377961602, message_id: 4242 })) {
	const fetch = mockFetch(reply);
	const onpaired = vi.fn();
	render(ConfirmDialog);
	render(TangerineExchange, { api: createAccountApi(hooks, 1, fetch), accountId: 1, accounts: ACCOUNTS, onpaired });
	return { fetch, onpaired, user: userEvent.setup() };
}

const POST = { name: 'Вступить и написать' };
const PAIR = { name: 'Связать' };

async function pair(user: ReturnType<typeof userEvent.setup>, partner = 'twink') {
	await user.selectOptions(screen.getByRole('combobox', { name: 'Обмениваться с…' }), partner);
	await user.click(screen.getByRole('button', PAIR));
	const dialog = await screen.findByRole('dialog');
	await user.click(within(dialog).getByRole('button', PAIR));
}

describe('«Обмен мандаринами»', () => {
	beforeEach(() => dialogs.answer(null));
	afterEach(() => vi.unstubAllGlobals());

	it('«Вступить и написать»: POST с текстом, id сообщения, копирование и подсказка', async () => {
		const writeText = vi.fn(async () => {});
		const { fetch, user } = block();
		vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
		const text = screen.getByRole('textbox', { name: 'Текст сообщения' });
		expect(text).toHaveValue('🍊');
		await user.clear(text);
		await user.type(text, '  привет  ');
		await user.click(screen.getByRole('button', POST));
		expect(await screen.findByText('Сообщение отправлено: id 4242')).toBeInTheDocument();
		expect(screen.getByText('Впиши этот id другому аккаунту в „Сообщение для /gt“')).toBeInTheDocument();
		expect(fetch.calls.map((c) => [c.method, c.url, c.body])).toEqual([
			['POST', '/api/v1/accounts/1/tangerine/post', JSON.stringify({ text: 'привет' })]
		]);
		await user.click(screen.getByRole('button', { name: 'Копировать' }));
		expect(writeText).toHaveBeenCalledWith('4242');
	});

	it('пустой текст — кнопки выключены, подсказка; длиннее 200 — тоже', async () => {
		const { fetch, user } = block();
		const text = screen.getByRole('textbox', { name: 'Текст сообщения' });
		await user.clear(text);
		await user.type(text, '   ');
		expect(screen.getByRole('button', POST)).toBeDisabled();
		expect(screen.getByRole('button', PAIR)).toBeDisabled();
		expect(screen.getByText('Текст: от 1 до 200 символов')).toBeInTheDocument();
		await user.clear(text);
		await user.type(text, '🍊'.repeat(201));
		expect(screen.getByRole('button', POST)).toBeDisabled();
		await user.clear(text);
		await user.type(text, '🍊'.repeat(200));
		expect(screen.getByRole('button', POST)).toBeEnabled();
		expect(screen.queryByText('Текст: от 1 до 200 символов')).toBeNull();
		expect(fetch.calls).toEqual([]);
	});

	it('список партнёров: без текущего аккаунта, не в сети — выключен с пометкой', () => {
		block();
		const select = screen.getByRole('combobox', { name: 'Обмениваться с…' });
		const options = within(select).getAllByRole('option');
		expect(options.map((o) => o.textContent?.trim())).toEqual([
			'— выберите аккаунт —',
			'twink',
			'old (Telegram не в сети)'
		]);
		expect(within(select).getByRole('option', { name: 'twink' })).toBeEnabled();
		expect(within(select).getByRole('option', { name: 'old (Telegram не в сети)' })).toBeDisabled();
		expect(screen.getByRole('button', PAIR)).toBeDisabled();
	});

	it('адресат не задан — строки адресата нет и запроса тоже', () => {
		const { fetch } = block();
		expect(fetch.calls).toEqual([]);
		expect(screen.queryByText(/🍊/)).toBeNull();
	});

	it('других аккаунтов нет — так и написано, связывать не с кем', () => {
		const fetch = mockFetch(() => json({}));
		render(TangerineExchange, { api: createAccountApi(hooks, 1, fetch), accountId: 1, accounts: [account(1, 'main')], onpaired: vi.fn() });
		expect(screen.getByText('Других аккаунтов нет')).toBeInTheDocument();
		expect(screen.queryByRole('button', PAIR)).toBeNull();
	});

	it('«Связать»: подтверждение с текстом, POST pair, «Связано: A ↔ B», настройки перечитываются', async () => {
		const { fetch, onpaired, user } = block(() =>
			json({ account: { id: 1, message_id: 10 }, partner: { id: 2, message_id: 11 } })
		);
		await user.selectOptions(screen.getByRole('combobox', { name: 'Обмениваться с…' }), 'twink');
		await user.click(screen.getByRole('button', PAIR));
		const dialog = await screen.findByRole('dialog');
		expect(dialog).toHaveTextContent('Оба аккаунта вступят в чат мандаринов и напишут „🍊“; каждому впишется сообщение другого');
		await user.click(within(dialog).getByRole('button', PAIR));
		expect(await screen.findByText('Связано: main ↔ twink')).toBeInTheDocument();
		expect(onpaired).toHaveBeenCalledOnce();
		expect(fetch.calls.map((c) => [c.method, c.url, c.body])).toEqual([
			['POST', '/api/v1/accounts/1/tangerine/pair', JSON.stringify({ partner_id: 2, text: '🍊' })]
		]);
	});

	it('отказ в подтверждении — запроса нет', async () => {
		const { fetch, user } = block();
		await user.selectOptions(screen.getByRole('combobox', { name: 'Обмениваться с…' }), 'twink');
		await user.click(screen.getByRole('button', PAIR));
		await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Отмена' }));
		expect(fetch.calls).toEqual([]);
	});

	it('партнёр не смог написать — чьё сообщение осталось в чате и что настройки не изменены', async () => {
		const { onpaired, user } = block(() =>
			json({ detail: 'tangerine_pair_partial', reason: 'CHAT_WRITE_FORBIDDEN', posted: { '1': 10 }, failed: 2, written: [] }, 502)
		);
		await pair(user);
		expect((await screen.findByRole('alert')).textContent?.trim()).toBe(
			'twink не смог написать (Telegram отказал: CHAT_WRITE_FORBIDDEN); сообщение main (id 10) осталось в чате, настройки не изменены'
		);
		expect(onpaired).not.toHaveBeenCalled();
	});

	it('партнёр упёрся в flood wait — ожидание в тексте', async () => {
		const { user } = block(() =>
			json({ detail: 'tangerine_pair_partial', reason: 'flood_wait', posted: { '1': 10 }, failed: 2, written: [] }, 429, { 'Retry-After': '42' })
		);
		await pair(user);
		expect(await screen.findByRole('alert')).toHaveTextContent(
			'twink не смог написать (Telegram просит подождать 42 с); сообщение main (id 10) осталось в чате'
		);
	});

	it('оба написали, настройка записалась не у всех — id сообщений, что вписать вручную; форма перечитывается', async () => {
		const { onpaired, user } = block(() =>
			json(
				{ detail: 'tangerine_pair_partial', reason: 'engine not running', posted: { '1': 10, '2': 11 }, failed: 2, written: [1] },
				503
			)
		);
		await pair(user);
		expect((await screen.findByRole('alert')).textContent?.trim()).toBe(
			'Оба сообщения в чате: main — id 10, twink — id 11. Настройки записались не у всех (Движок недоступен): ' +
				'у main записано. Впиши вручную в „Сообщение для /gt“: twink — 10'
		);
		expect(onpaired).toHaveBeenCalledOnce();
	});

	it('оба написали, не записалось ни у кого — оба id вручную, форма не перечитывается', async () => {
		const { onpaired, user } = block(() =>
			json(
				{ detail: 'tangerine_pair_partial', reason: 'settings_write_failed', posted: { '1': 10, '2': 11 }, failed: 1, written: [] },
				500
			)
		);
		await pair(user);
		expect((await screen.findByRole('alert')).textContent?.trim()).toBe(
			'Оба сообщения в чате: main — id 10, twink — id 11. Настройки записались не у всех (Сбой записи настроек): ' +
				'Впиши вручную в „Сообщение для /gt“: main — 11, twink — 10'
		);
		expect(onpaired).not.toHaveBeenCalled();
	});

	it.each([
		[409, { detail: 'tg_not_online', account_id: 2 }, {}, 'Telegram не в сети у twink — ничего не отправлено'],
		[409, { detail: 'tangerine_chat_mismatch' }, {}, 'Чат @mandarinkaSW не совпадает с «Чатом мандаринов» из настроек — ничего не отправлено'],
		[422, { detail: 'tangerine_pair_self' }, {}, 'Нельзя связать аккаунт с самим собой'],
		[502, { detail: 'join_request_sent' }, {}, 'Заявка на вступление в чат ждёт одобрения — сообщение не отправлено'],
		[429, { detail: 'flood_wait' }, { 'Retry-After': '7' }, 'Telegram просит подождать 7 с']
	])('ошибка пары %i %j — текст для человека', async (code, body, headers, text) => {
		const { onpaired, user } = block(() => json(body, code, headers));
		await pair(user);
		expect((await screen.findByRole('alert')).textContent?.trim()).toBe(text);
		expect(onpaired).not.toHaveBeenCalled();
	});

	it.each([
		[409, 'tg_not_online', 'Telegram не в сети'],
		[502, 'message_id_unknown', 'Telegram не вернул id отправленного сообщения'],
		[502, 'CHAT_WRITE_FORBIDDEN', 'Telegram отказал: CHAT_WRITE_FORBIDDEN'],
		[503, 'engine not running', 'Движок недоступен']
	])('ошибка «Вступить и написать» %i %s — текст для человека', async (code, detail, text) => {
		const { user } = block(() => json({ detail }, code));
		await user.click(screen.getByRole('button', POST));
		expect((await screen.findByRole('alert')).textContent?.trim()).toBe(text);
	});

	it('во время запроса обе кнопки выключены', async () => {
		let release: (r: Response) => void = () => {};
		const { user } = block(() => new Promise<Response>((r) => (release = r)));
		await user.selectOptions(screen.getByRole('combobox', { name: 'Обмениваться с…' }), 'twink');
		await user.click(screen.getByRole('button', POST));
		expect(screen.getByRole('button', POST)).toBeDisabled();
		expect(screen.getByRole('button', PAIR)).toBeDisabled();
		release(json({ chat_id: 1, message_id: 5 }));
		await screen.findByText('Сообщение отправлено: id 5');
		expect(screen.getByRole('button', POST)).toBeEnabled();
	});
});

describe('«Обмен мандаринами» в настройках', () => {
	beforeEach(() => dialogs.answer(null));

	const settings = fixture<SettingsOut>('settings');
	const paired: SettingsOut = {
		...settings,
		version: 14,
		values: { ...settings.values, chats: { ...(settings.values.chats as object), tangerine_reply_to: 11 } }
	};

	it('в карточке «Мандарин» после «Сообщения для /gt»; после пары поле показывает новое значение', async () => {
		let current = settings;
		const fetch = mockFetch((c) => {
			if (c.url.startsWith('/api/v1/accounts/1/settings/history')) return json(fixture('settings_history'));
			if (c.url === '/api/v1/accounts/1/tangerine/pair') {
				current = paired;
				return json({ account: { id: 1, message_id: 10 }, partner: { id: 2, message_id: 11 } });
			}
			if (c.url === '/api/v1/accounts/1/tangerine/partner') {
				const replyTo = (current.values.chats as { tangerine_reply_to: number }).tangerine_reply_to;
				return replyTo === 11
					? json({ reply_to: 11, sender: { tg_user_id: 102, name: 'Твинк', username: null }, account: { id: 2, name: 'twink' }, status: 'ok' })
					: json({ reply_to: replyTo, sender: { tg_user_id: 9, name: 'Анна', username: 'anna' }, account: null, status: 'ok' });
			}
			return json(current);
		});
		const api = createAccountApi(hooks, 1, fetch);
		const editor = new SettingsEditor(api);
		await editor.load();
		const user = userEvent.setup();
		render(ConfirmDialog);
		render(SettingsView, { api, editor, accountId: 1, accounts: ACCOUNTS });
		expect(screen.queryByRole('region', { name: 'Обмен мандаринами' })).toBeNull();
		await user.click(
			within(screen.getByRole('navigation', { name: 'Группы настроек' })).getByRole('button', { name: 'Мандарины и чаты' })
		);
		const section = screen.getByRole('region', { name: 'Мандарин' });
		expect(within(screen.getByRole('region', { name: 'Чаты' })).queryByRole('region', { name: 'Обмен мандаринами' })).toBeNull();
		const exchange = within(section).getByRole('region', { name: 'Обмен мандаринами' });
		const field = within(section).getByRole('spinbutton', { name: 'Сообщение для /gt' });
		expect(field.compareDocumentPosition(exchange) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		expect(field).toHaveValue(927136);
		expect(await within(exchange).findByText('🍊 дарим: Анна (@anna)')).toBeTruthy();
		await pair(user);
		await screen.findByText('Связано: main ↔ twink');
		await vi.waitFor(() => expect(within(section).getByRole('spinbutton', { name: 'Сообщение для /gt' })).toHaveValue(11));
		expect(editor.version).toBe(14);
		// Адресат сменился — строка перечитана: теперь это свой аккаунт, со ссылкой.
		const link = await within(exchange).findByRole('link', { name: '🍊 обмен с twink' });
		expect(link.getAttribute('href')).toBe('/a/2');
		expect(fetch.calls.filter((c) => c.url.endsWith('/tangerine/partner'))).toHaveLength(2);
	});
});
