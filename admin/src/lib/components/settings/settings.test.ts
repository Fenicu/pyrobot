import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import type { SettingsOut } from '$lib/api/types';
import { SettingsEditor } from '$lib/settings/editor.svelte';
import { settingPaths } from '$lib/settings/paths.svelte';
import schemaJson from '$lib/settings/settings.schema.json';
import { toasts } from '$lib/stores/toasts.svelte';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import SettingsView from './SettingsView.svelte';

const settings = fixture<SettingsOut>('settings');

async function view(
	patch?: (c: Call) => Response,
	current: () => SettingsOut = () => settings,
	post?: (c: Call) => Response | Promise<Response>,
	running = true
) {
	const fetch = mockFetch((c) => {
		if (c.url.startsWith('/api/v1/accounts/1/settings/history')) return json(fixture('settings_history'));
		if (c.method === 'POST' && post) return post(c);
		if (c.method === 'PATCH' && patch) return patch(c);
		if (c.method === 'PATCH') return json({ version: 14, values: JSON.parse(c.body).changes ? settings.values : {}, changed: {}, restart_required: [] });
		return json(current());
	});
	const api = createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
	const editor = new SettingsEditor(api);
	await editor.load();
	render(SettingsView, { api, editor, running, now: new Date('2026-09-27T20:00:00Z') });
	return { fetch, editor };
}

describe('Настройки', () => {
	it('раздел «Сбор артефакта» — списки дел; запись сбора (только чтение) в меню не попадает', async () => {
		const user = userEvent.setup();
		const values = {
			...settings.values,
			artifacts: { book_low: ['walk', 'job'], book_high: ['learn'], fax: ['job'], light: ['walk'], lottery_on_start: true }
		};
		await view(undefined, () => ({ ...settings, schema: schemaJson, values }));
		const nav = screen.getByRole('navigation', { name: 'Секции настроек' });
		expect(nav).toHaveTextContent('Сбор артефакта');
		expect(nav).not.toHaveTextContent('Текущий сбор артефакта');
		await user.click(screen.getByRole('button', { name: 'Сбор артефакта' }));
		const section = screen.getByRole('region', { name: 'Сбор артефакта' });
		expect(within(section).getByRole('group', { name: '📕 Букварь до 17 ур.' })).toHaveTextContent('walk');
		expect(within(section).getByRole('switch', { name: 'Лотерея при запуске' })).toBeChecked();
	});


	it('раздел «Поездки» — виды транспорта по-русски, порядок кнопками, секция после «Сбора артефакта»', async () => {
		const user = userEvent.setup();
		const values = {
			...settings.values,
			features: { ...(settings.values.features as object), trips: true },
			trips: { vehicles: ['car', 'bike'] }
		};
		const { editor } = await view(undefined, () => ({ ...settings, schema: schemaJson, values }));
		const nav = screen.getByRole('navigation', { name: 'Секции настроек' });
		const items = [...nav.querySelectorAll('li')].map((li) => li.textContent?.trim());
		expect(items.indexOf('Поездки')).toBe(items.indexOf('Сбор артефакта') + 1);
		await user.click(screen.getByRole('button', { name: 'Поездки' }));
		const section = screen.getByRole('region', { name: 'Поездки' });
		const list = within(section).getByRole('group', { name: 'Виды транспорта' });
		expect(list).toHaveTextContent('🚕 автомобиль');
		expect(list).toHaveTextContent('🚲 велосипед');
		expect(list).not.toHaveTextContent('car');
		await user.selectOptions(within(list).getByRole('combobox'), 'tram');
		expect(editor.value(['trips', 'vehicles'])).toEqual(['car', 'bike', 'tram']);
		expect(within(list).getByRole('option', { name: '🛷 санки' })).toBeInTheDocument();
	});


	it('разделы: сначала часто нужные, технические — в «Дополнительно» в конце', async () => {
		await view();
		expect(screen.getByRole('region', { name: 'Функции' })).toBeInTheDocument();
		const nav = screen.getByRole('navigation', { name: 'Секции настроек' });
		const items = [...nav.querySelectorAll('li')].map((li) => li.textContent?.trim());
		expect(items.slice(0, 4)).toEqual(['Функции', 'Стратегия и дела', 'Задания дня', 'Сон']);
		expect(items.slice(-2)).toEqual(['Дополнительно', 'Движок']);
	});

	it('секция: поля по типам, kill и пауза — только на главной, отличие от умолчания', async () => {
		const user = userEvent.setup();
		await view();
		await user.click(screen.getByRole('button', { name: 'Движок' }));
		const engine = screen.getByRole('region', { name: 'Движок' });
		expect(within(engine).getByRole('combobox', { name: 'Режим' })).toHaveValue('live');
		for (const path of ['engine.killed', 'engine.kill_reason', 'engine.paused']) {
			expect(engine.querySelector(`[data-path="${path}"]`)).toBeNull();
		}
		expect(engine).not.toHaveTextContent('только чтение');
		expect(engine).toHaveTextContent('Пауза и kill — кнопками на главной');
		expect(within(engine).getAllByRole('switch').length).toBe(2);
		await user.click(screen.getByRole('button', { name: 'Стратегия и дела' }));
		const strategy = screen.getByRole('region', { name: 'Стратегия и дела' });
		const deeds = strategy.querySelector('[data-path="strategy.deeds"]')!;
		expect(deeds).toHaveTextContent('не по умолч.');
		expect(within(strategy).getByRole('group', { name: 'Основные дела' })).toHaveTextContent('harvest');
	});

	it('пути настроек — только по переключателю, выбор запоминается', async () => {
		const user = userEvent.setup();
		localStorage.removeItem('pyrobot.settings.paths');
		settingPaths.set(false);
		await view();
		await user.click(screen.getByRole('button', { name: 'Сон' }));
		const row = screen.getByRole('region', { name: 'Сон' }).querySelector('[data-path="sleep.duration_h"]')!;
		expect(row).toHaveTextContent('Длительность сна, ч');
		expect(row).not.toHaveTextContent('sleep.duration_h');
		await user.click(screen.getByRole('checkbox', { name: 'пути настроек (для разработчика)' }));
		expect(row).toHaveTextContent('sleep.duration_h');
		expect(localStorage.getItem('pyrobot.settings.paths')).toBe('1');
		settingPaths.set(false);
	});

	it('правка → панель «N изменений · версия V» → сохранение', async () => {
		const user = userEvent.setup();
		const { fetch } = await view();
		await user.click(screen.getByRole('button', { name: 'Функции' }));
		await user.click(screen.getByRole('switch', { name: 'Казино' }));
		const bar = screen.getByRole('region', { name: 'Несохранённые изменения' });
		expect(bar).toHaveTextContent('1 изменение · версия 13');
		// До сохранения видно, что поменяется: раздел, название, было → станет.
		const diff = within(bar).getByRole('list', { name: 'Что поменяется' });
		expect(within(diff).getAllByRole('listitem').map((li) => li.textContent?.replace(/\s+/g, ' ').trim())).toEqual([
			'Функции · Казино: выкл → вкл'
		]);
		expect(screen.getByText('изменено')).toBeInTheDocument();
		await user.click(within(bar).getByRole('button', { name: 'Сохранить' }));
		await vi.waitFor(() => expect(fetch.calls.some((c) => c.method === 'PATCH')).toBe(true));
		expect(JSON.parse(fetch.calls.find((c) => c.method === 'PATCH')!.body)).toEqual({
			version: 13,
			changes: { features: { casino: true } },
			confirm_live: false
		});
		await vi.waitFor(() => expect(screen.queryByRole('region', { name: 'Несохранённые изменения' })).toBeNull());
	});

	it('настройка, которую бот не читает, — приглушена с пометкой, но меняется', async () => {
		const user = userEvent.setup();
		await view(undefined, () => ({ ...settings, schema: schemaJson }));
		await user.click(screen.getByRole('button', { name: 'Функции' }));
		const row = document.querySelector('[data-path="features.casino"]') as HTMLElement;
		// Приглушена подпись, а не вся строка: прозрачность съела бы контраст пути и фокуса в светлой теме.
		expect(row.className).not.toMatch(/opacity/);
		expect(row.querySelector('label')).toHaveClass('text-fg-muted');
		const note = within(row).getByText('не используется ботом');
		expect(note).toHaveClass('text-fg-muted');
		const toggle = within(row).getByRole('switch', { name: 'Казино' });
		expect(toggle.getAttribute('aria-describedby')!.split(' ')).toContain(note.id);
		await user.click(toggle);
		expect(screen.getByRole('region', { name: 'Несохранённые изменения' })).toHaveTextContent('1 изменение');
		const used = document.querySelector('[data-path="features.lottery"]') as HTMLElement;
		expect(used.querySelector('label')).not.toHaveClass('text-fg-muted');
		expect(used).not.toHaveTextContent('не используется ботом');
	});

	it('описание под полем, секции — своё; ⓘ раскрывает описание на телефоне', async () => {
		const user = userEvent.setup();
		await view();
		await user.click(screen.getByRole('button', { name: 'Движок' }));
		const engine = screen.getByRole('region', { name: 'Движок' });
		expect(engine).toHaveTextContent('Режим и темп шлюза');
		const row = engine.querySelector('[data-path="engine.min_request_interval_s"]')!;
		const help = within(row as HTMLElement).getByText(/Минимальный интервал между любыми двумя отправками/);
		// На ПК описание видно всегда (md:block), на телефоне скрыто до нажатия ⓘ.
		expect(help).toHaveClass('hidden', 'md:block');
		const info = within(row as HTMLElement).getByRole('button', { name: 'Описание: Пауза между запросами, с' });
		expect(info).toHaveAttribute('aria-expanded', 'false');
		expect(info).toHaveAttribute('aria-controls', help.id);
		expect(info).toHaveClass('md:hidden');
		// На ПК (описание всегда видно) поле связано с ним через aria-describedby.
		const field = within(row as HTMLElement).getByRole('spinbutton', { name: 'Пауза между запросами, с' });
		expect(field).toHaveAttribute('aria-describedby', help.id);
		info.focus();
		await user.keyboard('{Enter}');
		expect(info).toHaveAttribute('aria-expanded', 'true');
		expect(help).not.toHaveClass('hidden');
		await user.click(info);
		expect(help).toHaveClass('hidden');
	});

	it('поле-теги и поле-словарь связаны с описанием через aria-describedby', async () => {
		const user = userEvent.setup();
		await view();
		const search = screen.getByRole('searchbox', { name: 'Поиск настройки' });

		await user.type(search, 'strategy.focus');
		const focusRow = screen
			.getByRole('region', { name: 'Найденные настройки' })
			.querySelector('[data-path="strategy.focus"]') as HTMLElement;
		const focusHelp = within(focusRow).getByText(/Дела, которые бот делает/);
		expect(within(focusRow).getByRole('combobox', { name: 'Добавить в «Основные дела»' })).toHaveAttribute(
			'aria-describedby',
			focusHelp.id
		);

		await user.clear(search);
		await user.type(search, 'battle.overrides');
		const overridesRow = screen
			.getByRole('region', { name: 'Найденные настройки' })
			.querySelector('[data-path="battle.overrides"]') as HTMLElement;
		const overridesHelp = within(overridesRow).getByText(/Своя цель на битву/);
		const newKey = within(overridesRow).getByRole('textbox', { name: 'Цели по часу битвы (МСК): новый ключ' });
		expect(newKey).toHaveAttribute('aria-describedby', overridesHelp.id);
		await user.type(newKey, '5');
		await user.click(within(overridesRow).getByRole('button', { name: 'Добавить' }));
		expect(within(overridesRow).getByRole('combobox', { name: 'Цели по часу битвы (МСК): 5' })).toHaveAttribute(
			'aria-describedby',
			overridesHelp.id
		);
	});

	it('поиск находит настройку по описанию', async () => {
		const user = userEvent.setup();
		await view();
		await user.type(screen.getByRole('searchbox', { name: 'Поиск настройки' }), 'антифлуд');
		const found = screen.getByRole('region', { name: 'Найденные настройки' });
		const paths = [...found.querySelectorAll('[data-path]')].map((e) => e.getAttribute('data-path'));
		// «Пауза между запросами» — только по описанию: в пути и подписи слова нет.
		expect(paths).toContain('engine.min_request_interval_s');
		expect(paths).toContain('engine.antiflood_retry_max');
	});

	it('поиск находит поля по описанию секции и вложенной группы', async () => {
		const user = userEvent.setup();
		await view();
		const search = screen.getByRole('searchbox', { name: 'Поиск настройки' });
		const paths = () =>
			[...screen.getByRole('region', { name: 'Найденные настройки' }).querySelectorAll('[data-path]')].map((e) =>
				e.getAttribute('data-path')
			);
		// Только в описании группы «Не тратить на билеты».
		await user.type(search, 'на другие траты');
		expect(paths()).toEqual(['lottery.keep.money', 'lottery.keep.knowledge', 'lottery.keep.raw', 'lottery.keep.details']);
		// Только в описании секции «Движок».
		await user.clear(search);
		await user.type(search, 'темп шлюза');
		expect(paths()).toContain('engine.mode');
		expect(paths()).toContain('engine.manual_while_paused');
		expect(paths().every((p) => p?.startsWith('engine.'))).toBe(true);
	});

	it('«max» или число, теги, поиск', async () => {
		const user = userEvent.setup();
		const { editor } = await view();
		await user.type(screen.getByRole('searchbox', { name: 'Поиск настройки' }), 'lottery.tickets');
		const found = screen.getByRole('region', { name: 'Найденные настройки' });
		await user.selectOptions(within(found).getByRole('combobox', { name: 'Билеты за 💵: вид' }), 'num');
		expect(editor.value(['lottery', 'tickets', 'money'])).toBe(0);
		await user.clear(screen.getByRole('spinbutton', { name: 'Билеты за 💵: число' }));
		await user.type(screen.getByRole('spinbutton', { name: 'Билеты за 💵: число' }), '4');
		expect(editor.value(['lottery', 'tickets', 'money'])).toBe(4);
		await user.clear(screen.getByRole('searchbox', { name: 'Поиск настройки' }));
		await user.type(screen.getByRole('searchbox', { name: 'Поиск настройки' }), 'focus');
		await user.click(screen.getByRole('button', { name: 'dconv: раньше' }));
		expect(editor.value(['strategy', 'focus'])).toEqual(['dconv', 'harvest']);
		await user.click(screen.getByRole('button', { name: 'Убрать harvest' }));
		expect(editor.value(['strategy', 'focus'])).toEqual(['dconv']);
	});

	it('история: список версий и полный diff по клику', async () => {
		const user = userEvent.setup();
		await view();
		const list = await screen.findByRole('list', { name: 'Версии настроек' });
		const v8 = await within(list).findByRole('button', { name: /v8 / });
		expect(v8).toHaveTextContent(
			'Функции · Ежедневные задания → вкл; Стратегия и дела · Разрешённые дела → harvest, job, learn, dconv, walk, confa'
		);
		await user.click(v8);
		expect(v8).toHaveAttribute('aria-expanded', 'true');
		expect(within(list).getByLabelText('Изменения версии 8')).toHaveTextContent('Функции · Ежедневные задания выкл → вкл');
		expect(within(list).getByLabelText('Изменения версии 8')).not.toHaveTextContent('features.daily_tasks');
	});

	it('история: «вернуть» кладёт прежнее значение в черновик, сохранение — как обычно', async () => {
		const user = userEvent.setup();
		const { editor } = await view();
		const list = await screen.findByRole('list', { name: 'Версии настроек' });
		await user.click(await within(list).findByRole('button', { name: /v13 / }));
		await user.click(within(list).getByRole('button', { name: 'Вернуть «Функции · Лотерея»: выкл' }));
		expect(editor.value(['features', 'lottery'])).toBe(false);
		const bar = screen.getByRole('region', { name: 'Несохранённые изменения' });
		expect(bar).toHaveTextContent('Функции · Лотерея: вкл → выкл');
		// Значение уже в черновике — второй раз вернуть нечего.
		expect(within(list).queryByRole('button', { name: /Вернуть «Функции · Лотерея»/ })).toBeNull();
		// Паузу меняют кнопки на главной — вернуть её из истории нельзя.
		await user.click(within(list).getByRole('button', { name: /v12 / }));
		expect(within(list).getByLabelText('Изменения версии 12')).toHaveTextContent('Движок · Пауза планировщика вкл → выкл');
		expect(within(within(list).getByLabelText('Изменения версии 12')).queryByRole('button')).toBeNull();
	});

	it('чужая версия из SSE перечитывает и историю', async () => {
		let version = 13;
		const { fetch, editor } = await view(undefined, () => ({ ...settings, version }));
		const history = () => fetch.calls.filter((c) => c.url.startsWith('/api/v1/accounts/1/settings/history')).length;
		await vi.waitFor(() => expect(history()).toBe(1));
		version = 14;
		editor.onEvent({ type: 'settings', id: 'e:1', data: { version: 14, mode: 'live', paused: false, killed: false } });
		await vi.waitFor(() => expect(editor.version).toBe(14));
		await vi.waitFor(() => expect(history()).toBe(2));
	});

	it('422 без подходящего поля — сообщение с текстом ошибки, а не «поля подсвечены»', async () => {
		const user = userEvent.setup();
		toasts.items = [];
		await view(() =>
			json({ detail: [{ loc: ['body', 'changes', 'features'], msg: 'Value error, bad combo', type: 'value_error' }] }, 422)
		);
		await user.click(screen.getByRole('button', { name: 'Функции' }));
		await user.click(screen.getByRole('switch', { name: 'Казино' }));
		await user.click(screen.getByRole('button', { name: 'Сохранить' }));
		await vi.waitFor(() => expect(toasts.items.map((t) => t.text)).toEqual(['features: Value error, bad combo']));
	});
});

describe('Перезапуск аккаунта после сохранения', () => {
	const restartUrl = '/api/v1/accounts/1/engine/restart';

	// Сохранение, после которого сервер просит перезапуск: плашка с кнопкой.
	async function saved(post: (c: Call) => Response | Promise<Response>, running = true) {
		const user = userEvent.setup();
		const { fetch } = await view(
			() => json({ version: 14, values: settings.values, changed: {}, restart_required: ['chats.game_chat_id'] }),
			() => settings,
			post,
			running
		);
		await user.click(screen.getByRole('button', { name: 'Функции' }));
		await user.click(screen.getByRole('switch', { name: 'Казино' }));
		await user.click(screen.getByRole('button', { name: 'Сохранить' }));
		const banner = await screen.findByRole('status');
		return { user, fetch, banner };
	}

	it('плашка после PATCH с restart_required: что применится и кнопка «Перезапустить аккаунт»', async () => {
		const { banner, fetch } = await saved(() => new Response(null, { status: 202 }));
		expect(banner).toHaveTextContent('Изменения вступят в силу после перезапуска аккаунта: chats.game_chat_id');
		expect(within(banner).getByRole('button', { name: 'Перезапустить аккаунт' })).toBeEnabled();
		expect(fetch.calls.some((c) => c.url === restartUrl)).toBe(false);
	});

	it('движок не запущен (прямая запись): без кнопки — применится при включении', async () => {
		const { banner, fetch } = await saved(() => new Response(null, { status: 202 }), false);
		expect(banner).toHaveTextContent('Изменения вступят в силу при включении аккаунта: chats.game_chat_id');
		expect(banner).not.toHaveTextContent('после перезапуска');
		expect(within(banner).queryByRole('button')).toBeNull();
		expect(fetch.calls.some((c) => c.url === restartUrl)).toBe(false);
	});

	it('без restart_required плашки нет', async () => {
		const user = userEvent.setup();
		await view();
		await user.click(screen.getByRole('button', { name: 'Функции' }));
		await user.click(screen.getByRole('switch', { name: 'Казино' }));
		await user.click(screen.getByRole('button', { name: 'Сохранить' }));
		await vi.waitFor(() => expect(screen.queryByRole('region', { name: 'Несохранённые изменения' })).toBeNull());
		expect(screen.queryByRole('button', { name: 'Перезапустить аккаунт' })).toBeNull();
	});

	it('клик → POST /engine/restart с CSRF; пока запрос идёт, кнопка неактивна; 202 → «Аккаунт перезапускается»', async () => {
		let answer!: (r: Response) => void;
		const pending = new Promise<Response>((resolve) => (answer = resolve));
		const { user, fetch, banner } = await saved(() => pending);
		await user.click(within(banner).getByRole('button', { name: 'Перезапустить аккаунт' }));
		await vi.waitFor(() => expect(within(banner).getByRole('button', { name: 'Перезапустить аккаунт' })).toBeDisabled());
		const posts = fetch.calls.filter((c) => c.method === 'POST');
		expect(posts.map((c) => c.url)).toEqual([restartUrl]);
		expect(posts[0]?.headers.get('x-csrf-token')).toBe('c');
		answer(new Response(null, { status: 202 }));
		await vi.waitFor(() => expect(banner).toHaveTextContent('Аккаунт перезапускается'));
		expect(banner).not.toHaveTextContent('Изменения вступят в силу');
		expect(within(banner).queryByRole('button')).toBeNull();
	});

	it('503 engine not running → «Движок недоступен», плашка с кнопкой остаётся, можно повторить', async () => {
		let calls = 0;
		const { user, banner } = await saved(() => {
			calls += 1;
			return calls === 1 ? json({ detail: 'engine not running' }, 503) : new Response(null, { status: 202 });
		});
		await user.click(within(banner).getByRole('button', { name: 'Перезапустить аккаунт' }));
		expect(await within(banner).findByRole('alert')).toHaveTextContent('Движок недоступен');
		expect(banner).toHaveTextContent('Изменения вступят в силу после перезапуска аккаунта');
		await user.click(within(banner).getByRole('button', { name: 'Перезапустить аккаунт' }));
		await vi.waitFor(() => expect(banner).toHaveTextContent('Аккаунт перезапускается'));
		expect(within(banner).queryByRole('alert')).toBeNull();
	});

	it('409 account_deleting → «Аккаунт удаляется»', async () => {
		const { user, banner } = await saved(() => json({ detail: 'account_deleting' }, 409));
		await user.click(within(banner).getByRole('button', { name: 'Перезапустить аккаунт' }));
		expect(await within(banner).findByRole('alert')).toHaveTextContent('Аккаунт удаляется');
		expect(within(banner).getByRole('button', { name: 'Перезапустить аккаунт' })).toBeEnabled();
	});
});
