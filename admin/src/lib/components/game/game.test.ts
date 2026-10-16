import { fireEvent, render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import type { EngineStatus, MessageItem } from '$lib/api/types';
import type { LiveEvent, SseMessage } from '$lib/live/sse';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { GameChat } from '$lib/game/store.svelte';
import GameView from './GameView.svelte';

const GAME = 227859379;

const item = (id: number, msg_id: number, patch: Partial<MessageItem> = {}): MessageItem => ({
	type: 'message',
	id,
	at: '2026-10-17T10:00:01Z',
	chat_id: GAME,
	msg_id,
	revision: 0,
	kind: 'new',
	date: '2026-10-17T10:00:00Z',
	outgoing: false,
	recovered: false,
	text: `#${msg_id}`,
	markup: null,
	events: [],
	source: null,
	...patch
});

const HISTORY = [
	item(5, 14, {
		text: 'Где собираешься спать?',
		kind: 'edit',
		revision: 1790535904,
		markup: {
			inline: [
				['Под мостом - 0 💵', 0, 0, 'sleep_Bridge', null, null],
				['В отеле - 213 💵', 1, 0, 'sleep_Hotel', null, null],
				['Канал', 2, 0, null, 'https://t.me/startupwars', null]
			]
		}
	}),
	item(4, 13, { text: '/sleep', outgoing: true, source: 'manual' }),
	item(3, 12, { text: 'Профиль\nстрока 2', markup: { reply: [['🎒Рюкзак', '🛌Сон'], ['📊Профиль']] } }),
	item(2, 11, { text: '/me', outgoing: true, source: 'planner' }),
	item(1, 10, { text: '<b>не html</b>' })
];

const live = (msg_id: number, patch: Partial<SseMessage> = {}): LiveEvent => ({
	type: 'message',
	id: 'e:1',
	data: {
		journal_id: 900 + msg_id,
		chat_id: GAME,
		msg_id,
		revision: 0,
		kind: 'new',
		date: '2026-10-17T10:05:00Z',
		outgoing: false,
		text: `live ${msg_id}`,
		events: [],
		markup: null,
		...patch
	}
});

const engine = (patch: Partial<EngineStatus> = {}): EngineStatus => ({
	...fixture<EngineStatus>('engine_status'),
	running: true,
	...patch
});

function setup(
	handler: (c: Call) => Response | undefined = () => undefined,
	opts: { items?: MessageItem[]; next?: string | null; status?: EngineStatus | null } = {}
) {
	const listeners = new Set<(e: LiveEvent) => void>();
	const fetch = mockFetch((c) => {
		const custom = handler(c);
		if (custom) return custom;
		if (c.url === '/api/v1/accounts/1/settings') return json(fixture('settings'));
		if (c.url.startsWith('/api/v1/accounts/1/journal?')) {
			return json({ items: opts.items ?? HISTORY, next_cursor: opts.next ?? null });
		}
		return json({ detail: 'Not Found' }, 404);
	});
	const api = createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
	const confirmer = vi.fn(async () => true);
	const subscribe = (fn: (e: LiveEvent) => void) => {
		listeners.add(fn);
		return () => listeners.delete(fn);
	};
	const status = opts.status === undefined ? engine() : opts.status;
	render(GameView, { api, chat: new GameChat(api), subscribe, confirmer, status });
	const emit = (e: LiveEvent) => listeners.forEach((fn) => fn(e));
	return { fetch, confirmer, emit, user: userEvent.setup() };
}

const log = () => screen.getByRole('log', { name: 'Сообщения игры' });
const bubble = (text: string | RegExp) => within(log()).getByText(text).closest('article')!;
const posts = (fetch: ReturnType<typeof mockFetch>, path: string) =>
	fetch.calls.filter((c) => c.method === 'POST' && c.url === `/api/v1/accounts/1${path}`).map((c) => JSON.parse(c.body));

describe('Игра: чат', () => {
	it('входящие слева, свои справа; бот помечен; текст — как текст, с переносами', async () => {
		setup();
		await within(log()).findByText('Где собираешься спать?');
		expect(bubble('/me')).toHaveAttribute('data-side', 'out');
		expect(within(bubble('/me')).getByTitle('отправил бот')).toBeInTheDocument();
		expect(bubble('/sleep')).toHaveAttribute('data-side', 'out');
		expect(within(bubble('/sleep')).queryByTitle('отправил бот')).toBeNull();
		expect(bubble(/Профиль/)).toHaveAttribute('data-side', 'in');
		expect(bubble(/Профиль/).textContent).toContain('Профиль\nстрока 2');
		expect(within(log()).getByText('<b>не html</b>')).toBeInTheDocument();
		// Порядок — по msg_id: старое сверху.
		const order = within(log()).getAllByRole('article').map((a) => a.getAttribute('data-msg'));
		expect(order).toEqual(['10', '11', '12', '13', '14']);
		expect(within(bubble('Где собираешься спать?')).getByText(/изм\./)).toBeInTheDocument();
		expect(within(bubble('Где собираешься спать?')).getByText(/13:00/)).toBeInTheDocument();
	});

	it('пусто — подсказка', async () => {
		setup(undefined, { items: [] });
		expect(await screen.findByText('Сообщений с игрой пока нет')).toBeInTheDocument();
	});

	it('ошибка загрузки — текст и повтор', async () => {
		let fail = true;
		const { user } = setup((c) => (c.url.includes('/journal') && fail ? json({ detail: 'boom' }, 500) : undefined));
		const retry = await screen.findByRole('button', { name: 'Повторить' });
		fail = false;
		await user.click(retry);
		expect(await within(log()).findByText('Где собираешься спать?')).toBeInTheDocument();
	});

	it('кнопка сообщения: клик с последней ревизией; отказ — системной строкой в чате', async () => {
		const { fetch, user } = setup((c) =>
			c.url.endsWith('/commands/click') ? json({ action_id: 9, status: 'rejected', reason: 'tg_unreachable', answer: null }) : undefined
		);
		await within(log()).findByText('Где собираешься спать?');
		await user.click(screen.getByRole('button', { name: 'В отеле - 213 💵' }));
		expect(await within(log()).findByText('«В отеле - 213 💵»: Отклонено: нет связи с Telegram')).toBeInTheDocument();
		expect(posts(fetch, '/commands/click')[0]).toMatchObject({
			chat_id: GAME,
			message_id: 14,
			revision: 1790535904,
			callback_data: 'sleep_Hotel'
		});
	});

	it('после правки сообщения кнопка жмётся с новой ревизией; ссылка — в новой вкладке', async () => {
		const { fetch, emit, user } = setup((c) =>
			c.url.endsWith('/commands/click') ? json({ action_id: 9, status: 'confirmed', reason: 'reply', answer: null }) : undefined
		);
		await within(log()).findByText('Где собираешься спать?');
		const link = screen.getByRole('link', { name: 'Канал' });
		expect(link).toHaveAttribute('href', 'https://t.me/startupwars');
		expect(link).toHaveAttribute('target', '_blank');
		emit(live(14, { kind: 'edit', revision: 1790535999, text: 'Где спать? (2)', markup: { inline: [['Под мостом - 0 💵', 0, 0, 'sleep_Bridge', null, null]] } }));
		await user.click(await screen.findByRole('button', { name: 'Под мостом - 0 💵' }));
		await vi.waitFor(() => expect(posts(fetch, '/commands/click')).toHaveLength(1));
		expect(posts(fetch, '/commands/click')[0]).toMatchObject({ revision: 1790535999, callback_data: 'sleep_Bridge' });
		expect(within(log()).queryByText(/Отклонено/)).toBeNull();
	});

	it('кнопка в ожидании — занята, повторно не жмётся', async () => {
		let release!: (r: Response) => void;
		const { fetch, user } = setup((c) =>
			c.url.endsWith('/commands/click') ? (new Promise<Response>((r) => (release = r)) as unknown as Response) : undefined
		);
		await within(log()).findByText('Где собираешься спать?');
		const hotel = screen.getByRole('button', { name: 'В отеле - 213 💵' });
		await user.click(hotel);
		expect(hotel).toHaveAttribute('aria-busy', 'true');
		expect(screen.getByRole('button', { name: 'Под мостом - 0 💵' })).toBeDisabled();
		release(json({ action_id: 9, status: 'confirmed', reason: '', answer: null }));
		await vi.waitFor(() => expect(hotel).not.toHaveAttribute('aria-busy'));
		expect(posts(fetch, '/commands/click')).toHaveLength(1);
	});

	it('клавиатура игры — от последнего входящего с ней; нажатие отправляет текст', async () => {
		const { fetch, user } = setup((c) =>
			c.url.endsWith('/commands/send') ? json({ action_id: 9, status: 'confirmed', reason: 'reply', answer: null }) : undefined
		);
		const keyboard = await screen.findByRole('group', { name: 'Клавиатура игры' });
		await user.click(within(keyboard).getByRole('button', { name: '🛌Сон' }));
		await vi.waitFor(() => expect(posts(fetch, '/commands/send')).toHaveLength(1));
		expect(posts(fetch, '/commands/send')[0]).toMatchObject({ text: '🛌Сон' });
		expect(posts(fetch, '/commands/send')[0].idempotency_key).toMatch(/^send-/);
	});

	it('Enter отправляет, Shift+Enter — перенос строки; отказ — строкой в чате', async () => {
		const { fetch, user } = setup((c) =>
			c.url.endsWith('/commands/send') ? json({ action_id: 9, status: 'rejected', reason: 'paused', answer: null }) : undefined
		);
		await within(log()).findByText('Где собираешься спать?');
		const input = screen.getByRole('textbox', { name: 'Сообщение в игру' });
		await user.type(input, '/inv{Shift>}{Enter}{/Shift}x');
		expect(input).toHaveValue('/inv\nx');
		expect(posts(fetch, '/commands/send')).toHaveLength(0);
		await user.clear(input);
		await user.type(input, '/inv{Enter}');
		await vi.waitFor(() => expect(posts(fetch, '/commands/send')).toHaveLength(1));
		expect(posts(fetch, '/commands/send')[0]).toMatchObject({ text: '/inv' });
		expect(await within(log()).findByText('«/inv»: Отклонено: пауза')).toBeInTheDocument();
		expect(input).toHaveValue('');
	});

	it('risky — окно подтверждения, повтор с токеном', async () => {
		const confirm = {
			code: 'confirm_required', reason: 'missing', confirm_token: 'tok', expires_at: '2026-10-17T10:02:00Z',
			state_version: 5, command_class: 'risky'
		};
		const { fetch, confirmer, user } = setup((c) => {
			if (!c.url.endsWith('/commands/send')) return undefined;
			return JSON.parse(c.body).confirm_token
				? json({ action_id: 7, status: 'confirmed', reason: 'reply', answer: null })
				: json({ detail: confirm }, 409);
		});
		await within(log()).findByText('Где собираешься спать?');
		await user.type(screen.getByRole('textbox', { name: 'Сообщение в игру' }), '/sells_piper_80');
		await user.click(screen.getByRole('button', { name: 'Отправить' }));
		await vi.waitFor(() => expect(posts(fetch, '/commands/send')).toHaveLength(2));
		expect(posts(fetch, '/commands/send')[1]).toEqual({ ...posts(fetch, '/commands/send')[0], confirm_token: 'tok' });
		expect(confirmer).toHaveBeenCalledWith(confirm, '/sells_piper_80');
	});

	it('движок не запущен — поле ввода выключено с пояснением', async () => {
		setup(undefined, { status: engine({ running: false }) });
		await within(log()).findByText('Где собираешься спать?');
		expect(screen.getByRole('textbox', { name: 'Сообщение в игру' })).toBeDisabled();
		expect(screen.getByText('Движок не запущен — команды в игру не уходят')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'В отеле - 213 💵' })).toBeDisabled();
	});

	it('поток: новое сообщение и правка — на месте, без дублей', async () => {
		const { emit } = setup();
		await within(log()).findByText('Где собираешься спать?');
		emit(live(15, { outgoing: true, source: 'planner', text: '/inv' }));
		emit(live(12, { kind: 'edit', revision: 5, text: 'Профиль (изм)' }));
		emit(live(16, { chat_id: -1001 }));
		await within(log()).findByText('/inv');
		expect(within(bubble('/inv')).getByTitle('отправил бот')).toBeInTheDocument();
		expect(within(log()).getByText('Профиль (изм)')).toBeInTheDocument();
		expect(within(log()).getAllByRole('article').map((a) => a.getAttribute('data-msg'))).toEqual(['10', '11', '12', '13', '14', '15']);
	});

	it('история раньше — кнопкой сверху, порядок сохраняется', async () => {
		const { fetch, user } = setup(
			(c) =>
				c.url.includes('cursor=c1')
					? json({ items: [item(0, 9, { text: 'самое старое' })], next_cursor: null })
					: undefined,
			{ next: 'c1' }
		);
		await within(log()).findByText('Где собираешься спать?');
		await user.click(screen.getByRole('button', { name: 'Показать раньше' }));
		await within(log()).findByText('самое старое');
		expect(within(log()).getAllByRole('article').map((a) => a.getAttribute('data-msg'))).toEqual(['9', '10', '11', '12', '13', '14']);
		expect(screen.queryByRole('button', { name: 'Показать раньше' })).toBeNull();
		expect(fetch.calls.filter((c) => c.url.includes('/journal'))).toHaveLength(2);
	});

	it('новое, пока чат прокручен вверх, — значок «новые», по нему вниз', async () => {
		const { emit, user } = setup();
		await within(log()).findByText('Где собираешься спать?');
		const el = log();
		Object.defineProperty(el, 'scrollHeight', { configurable: true, value: 2000 });
		Object.defineProperty(el, 'clientHeight', { configurable: true, value: 400 });
		Object.defineProperty(el, 'scrollTop', { configurable: true, writable: true, value: 500 });
		await fireEvent.scroll(el);
		emit(live(15));
		const chip = await screen.findByRole('button', { name: /новые/ });
		await user.click(chip);
		expect(el.scrollTop).toBe(2000);
		expect(screen.queryByRole('button', { name: /новые/ })).toBeNull();
	});
});
