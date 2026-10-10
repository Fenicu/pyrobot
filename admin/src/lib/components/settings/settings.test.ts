import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { goto } from '$app/navigation';
import { createAccountApi } from '$lib/api/account';
import type { SettingsOut } from '$lib/api/types';
import { SettingsEditor } from '$lib/settings/editor.svelte';
import { settingPaths } from '$lib/settings/paths.svelte';
import schemaJson from '$lib/settings/settings.schema.json';
import { toasts } from '$lib/stores/toasts.svelte';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { page } from '$lib/test/page.svelte';
import SettingsView from './SettingsView.svelte';

vi.mock('$app/state', async () => ({ page: (await import('$lib/test/page.svelte')).page }));
vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => {}) }));

const settings = fixture<SettingsOut>('settings');

afterEach(() => {
	page.url = new URL('http://app.invalid/');
	vi.mocked(goto).mockClear();
});

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

const groups = () => screen.getByRole('navigation', { name: 'Группы настроек' });
const openGroup = (user: ReturnType<typeof userEvent.setup>, name: string) =>
	user.click(within(groups()).getByRole('button', { name }));
const card = (name: string, scope: HTMLElement = document.body) => within(scope).getByRole('region', { name });

/** Схема сервера новее клиента: флаг `features.new_flag`, которого нет в таблице механик. */
function newerServer(): SettingsOut {
	const schema = structuredClone(schemaJson) as unknown as {
		$defs: Record<string, { properties: Record<string, unknown> }>;
	};
	schema.$defs.FeaturesSection!.properties.new_flag = { type: 'boolean', title: 'New Flag', default: false };
	const withFlag = (v: Record<string, unknown>, on: boolean) => ({
		...v,
		features: { ...(v.features as object), new_flag: on }
	});
	return {
		...settings,
		schema: schema as unknown as SettingsOut['schema'],
		values: withFlag(settings.values, true),
		defaults: withFlag(settings.defaults as Record<string, unknown>, false)
	};
}

describe('Настройки', () => {
	it('группы механик по порядку, «Функций» нет; «Дополнительно» отделено, в нём пути для разработчика', async () => {
		await view(undefined, () => ({ ...settings, schema: schemaJson }));
		const items = within(groups())
			.getAllByRole('button')
			.map((b) => b.textContent?.replace(/\s+/g, ' ').trim());
		expect(items).toEqual([
			'Дела и прокачка 4',
			'Еда и сон 3',
			'Битва и деньги 7',
			'Метро и поездки 2',
			'Подарки и предметы 4',
			'Задания дня 2',
			'Мандарины и чаты 2',
			'Прочее 5',
			'Дополнительно 1'
		]);
		expect(screen.queryByRole('region', { name: 'Функции' })).toBeNull();
		expect(groups()).not.toHaveTextContent('Функции');
		// Первая группа открыта сразу.
		expect(screen.getByRole('region', { name: 'Дела и прокачка' })).toBeInTheDocument();
		const devPaths = within(groups()).getByRole('checkbox', { name: 'пути настроек (для разработчика)' });
		const advanced = within(groups()).getByRole('button', { name: 'Дополнительно' });
		expect(advanced.compareDocumentPosition(devPaths) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	});

	it('карточка механики: включатель в заголовке и её параметры', async () => {
		const user = userEvent.setup();
		await view();
		await openGroup(user, 'Метро и поездки');
		const metro = card('Метро');
		expect(within(metro).getByRole('switch', { name: 'Метро' })).toHaveAttribute('aria-checked', 'true');
		const paths = [...metro.querySelectorAll('[data-path]')].map((e) => e.getAttribute('data-path'));
		expect(paths).toContain('metro.chest_min_packs');
		expect(paths).toContain('metro.buffs');
		expect(paths.every((p) => p?.startsWith('metro.') || p === 'strategy.reserve_ahead_min.metro')).toBe(true);
		expect(metro).not.toHaveTextContent('умолч.:');
	});

	it('включатель в заголовке: точка у группы и строка «Метро · Включено» в панели', async () => {
		const user = userEvent.setup();
		const { fetch } = await view();
		await openGroup(user, 'Метро и поездки');
		const group = within(groups()).getByRole('button', { name: 'Метро и поездки' });
		expect(group).not.toHaveTextContent('•');
		await user.click(within(card('Метро')).getByRole('switch', { name: 'Метро' }));
		expect(group).toHaveTextContent('•');
		expect(group).toHaveAccessibleName('Метро и поездки, есть несохранённые правки');
		expect(within(groups()).getByRole('button', { name: 'Еда и сон' })).not.toHaveTextContent('•');
		const bar = screen.getByRole('region', { name: 'Несохранённые изменения' });
		expect(bar).toHaveTextContent('1 изменение · версия 13');
		const diff = within(bar).getByRole('list', { name: 'Что поменяется' });
		expect(within(diff).getAllByRole('listitem').map((li) => li.textContent?.replace(/\s+/g, ' ').trim())).toEqual([
			'Метро · Включено: вкл → выкл'
		]);
		await user.click(within(bar).getByRole('button', { name: 'Сохранить' }));
		await vi.waitFor(() => expect(fetch.calls.some((c) => c.method === 'PATCH')).toBe(true));
		expect(JSON.parse(fetch.calls.find((c) => c.method === 'PATCH')!.body)).toEqual({
			version: 13,
			changes: { features: { metro: false } },
			confirm_live: false
		});
		await vi.waitFor(() => expect(screen.queryByRole('region', { name: 'Несохранённые изменения' })).toBeNull());
	});

	it('поле не по умолчанию: синяя точка и «сбросить к умолчанию (X)» — возвращает умолчание', async () => {
		const user = userEvent.setup();
		const { editor } = await view();
		const deeds = card('Дела');
		expect(deeds).toHaveTextContent('1 не по умолчанию');
		const row = deeds.querySelector('[data-path="strategy.deeds"]') as HTMLElement;
		expect(within(row).getByTitle('Отличается от умолчания')).toBeInTheDocument();
		const focus = deeds.querySelector('[data-path="strategy.focus"]') as HTMLElement;
		expect(within(focus).queryByTitle('Отличается от умолчания')).toBeNull();
		expect(within(focus).queryByRole('button', { name: /сбросить к умолчанию/ })).toBeNull();
		await user.click(within(row).getByRole('button', { name: 'сбросить к умолчанию (harvest, job, learn, dconv, walk)' }));
		expect(editor.value(['strategy', 'deeds'])).toEqual(['harvest', 'job', 'learn', 'dconv', 'walk']);
		expect(within(row).queryByTitle('Отличается от умолчания')).toBeNull();
		expect(within(row).queryByRole('button', { name: /сбросить к умолчанию/ })).toBeNull();
		expect(deeds).not.toHaveTextContent('не по умолчанию');
		expect(screen.getByRole('region', { name: 'Несохранённые изменения' })).toHaveTextContent(
			'Дела · Разрешённые дела: harvest, job, learn, dconv, walk, confa → harvest, job, learn, dconv, walk'
		);
	});

	it('описание механики — первое предложение, «Подробнее» раскрывает полную справку', async () => {
		const user = userEvent.setup();
		await view();
		await openGroup(user, 'Метро и поездки');
		const metro = card('Метро');
		expect(metro).toHaveTextContent('Забеги в метро (вход — 2🔥) после кулдауна');
		expect(metro).not.toHaveTextContent('Включённое метро держит 2🔥');
		const more = within(metro).getByRole('button', { name: 'Подробнее' });
		expect(more).toHaveAttribute('aria-expanded', 'false');
		await user.click(more);
		expect(more).toHaveAttribute('aria-expanded', 'true');
		expect(metro).toHaveTextContent('Включённое метро держит 2🔥');
	});

	it('раздел «Сбор артефакта» — списки дел; запись сбора (только чтение) не показывается', async () => {
		const user = userEvent.setup();
		const values = {
			...settings.values,
			artifacts: { book_low: ['walk', 'job'], book_high: ['learn'], fax: ['job'], light: ['walk'], lottery_on_start: true }
		};
		await view(undefined, () => ({ ...settings, schema: schemaJson, values }));
		await openGroup(user, 'Подарки и предметы');
		expect(document.body).not.toHaveTextContent('Текущий сбор артефакта');
		const artifacts = card('Сбор артефакта');
		expect(within(artifacts).queryByRole('switch', { name: 'Сбор артефакта' })).toBeNull();
		expect(within(artifacts).getByRole('group', { name: '📕 Букварь до 17 ур.' })).toHaveTextContent('walk');
		expect(within(artifacts).getByRole('switch', { name: 'Лотерея при запуске' })).toBeChecked();
	});

	it('карточка «Поездки» — виды транспорта по-русски, порядок кнопками', async () => {
		const user = userEvent.setup();
		const values = {
			...settings.values,
			features: { ...(settings.values.features as object), trips: true },
			trips: { vehicles: ['car', 'bike'] }
		};
		const { editor } = await view(undefined, () => ({ ...settings, schema: schemaJson, values }));
		await openGroup(user, 'Метро и поездки');
		const list = within(card('Поездки')).getByRole('group', { name: 'Виды транспорта' });
		expect(list).toHaveTextContent('🚕 автомобиль');
		expect(list).toHaveTextContent('🚲 велосипед');
		expect(list).not.toHaveTextContent('car');
		await user.selectOptions(within(list).getByRole('combobox'), 'tram');
		expect(editor.value(['trips', 'vehicles'])).toEqual(['car', 'bike', 'tram']);
		expect(within(list).getByRole('option', { name: '🛷 санки' })).toBeInTheDocument();
	});

	it('каналы смузи и биржевиков — в карточках своих механик', async () => {
		const user = userEvent.setup();
		await view();
		await openGroup(user, 'Еда и сон');
		expect(card('Смузи').querySelector('[data-path="chats.smoothie_channel_id"]')).not.toBeNull();
		await openGroup(user, 'Битва и деньги');
		expect(card('Биржевики').querySelector('[data-path="chats.bulls_invite_chat_id"]')).not.toBeNull();
		expect(within(card('Защита от ограбления')).getByRole('switch', { name: 'Защита от ограбления' })).toBeInTheDocument();
	});

	it('«Движок»: поля по типам, kill и пауза — только на главной', async () => {
		const user = userEvent.setup();
		await view();
		await openGroup(user, 'Дополнительно');
		const engine = card('Движок');
		expect(within(engine).getByRole('combobox', { name: 'Режим' })).toHaveValue('live');
		for (const path of ['engine.killed', 'engine.kill_reason', 'engine.paused']) {
			expect(engine.querySelector(`[data-path="${path}"]`)).toBeNull();
		}
		expect(engine).not.toHaveTextContent('только чтение');
		expect(engine).toHaveTextContent('Режим и темп шлюза');
		await user.click(within(engine).getByRole('button', { name: 'Подробнее' }));
		expect(engine).toHaveTextContent('Пауза и kill — кнопками на главной');
		expect(within(engine).getAllByRole('switch').length).toBe(2);
		await openGroup(user, 'Дела и прокачка');
		expect(within(card('Дела')).getByRole('group', { name: 'Основные дела' })).toHaveTextContent('harvest');
	});

	it('поле, которого нет в таблице (сервер новее), — в «Прочих настройках», меняется', async () => {
		const user = userEvent.setup();
		const { editor } = await view(undefined, newerServer);
		await openGroup(user, 'Дополнительно');
		const other = card('Прочие настройки');
		const flag = within(other).getByRole('switch', { name: 'New Flag' });
		expect(flag).toBeChecked();
		expect(within(groups()).getByRole('button', { name: 'Дополнительно' })).toHaveTextContent('Дополнительно 2');
		await user.click(flag);
		expect(editor.value(['features', 'new_flag'])).toBe(false);
		expect(
			within(groups()).getByRole('button', { name: 'Дополнительно, есть несохранённые правки' })
		).toHaveTextContent('•');
		expect(screen.getByRole('region', { name: 'Несохранённые изменения' })).toHaveTextContent('Функции · New Flag: вкл → выкл');
	});

	it('без неизвестных полей «Прочих настроек» нет', async () => {
		const user = userEvent.setup();
		await view(undefined, () => ({ ...settings, schema: schemaJson }));
		await openGroup(user, 'Дополнительно');
		expect(screen.queryByRole('region', { name: 'Прочие настройки' })).toBeNull();
	});

	it('пути настроек — только по переключателю, выбор запоминается', async () => {
		const user = userEvent.setup();
		localStorage.removeItem('pyrobot.settings.paths');
		settingPaths.set(false);
		await view();
		await openGroup(user, 'Еда и сон');
		const row = card('Сон').querySelector('[data-path="sleep.duration_h"]')!;
		expect(row).toHaveTextContent('Длительность сна, ч');
		expect(row).not.toHaveTextContent('sleep.duration_h');
		await user.click(screen.getByRole('checkbox', { name: 'пути настроек (для разработчика)' }));
		expect(row).toHaveTextContent('sleep.duration_h');
		expect(localStorage.getItem('pyrobot.settings.paths')).toBe('1');
		settingPaths.set(false);
	});

	it('механика, которую бот не читает, — приглушена с пометкой, но включается', async () => {
		const user = userEvent.setup();
		await view(undefined, () => ({ ...settings, schema: schemaJson }));
		await openGroup(user, 'Прочее');
		const casino = card('Казино');
		const title = within(casino).getByRole('heading', { name: 'Казино' });
		expect(title).toHaveClass('text-fg-muted');
		const note = within(casino).getByText('не используется ботом');
		expect(note).toHaveClass('text-fg-muted');
		const toggle = within(casino).getByRole('switch', { name: 'Казино' });
		expect(toggle.getAttribute('aria-describedby')!.split(' ')).toContain(note.id);
		await user.click(toggle);
		expect(screen.getByRole('region', { name: 'Несохранённые изменения' })).toHaveTextContent('1 изменение');
		const used = card('Пир пета');
		expect(within(used).getByRole('heading', { name: 'Пир пета' })).not.toHaveClass('text-fg-muted');
		expect(used).not.toHaveTextContent('не используется ботом');
	});

	it('подсказка поля — первое предложение, ⓘ раскрывает полное описание', async () => {
		const user = userEvent.setup();
		await view();
		await openGroup(user, 'Дополнительно');
		const row = card('Движок').querySelector('[data-path="engine.min_request_interval_s"]') as HTMLElement;
		const help = within(row).getByText(/Минимальный интервал между любыми двумя отправками/);
		expect(help).not.toHaveClass('hidden');
		expect(help).not.toHaveTextContent('Применяется сразу');
		const info = within(row).getByRole('button', { name: 'Описание: Пауза между запросами, с' });
		expect(info).toHaveAttribute('aria-expanded', 'false');
		expect(info).toHaveAttribute('aria-controls', help.id);
		// Поле связано с подсказкой и с границей сервера (под полем «не меньше 1.6»).
		const field = within(row).getByRole('spinbutton', { name: 'Пауза между запросами, с' });
		expect(field.getAttribute('aria-describedby')!.split(' ')).toContain(help.id);
		expect(row).toHaveTextContent('не меньше 1.6');
		expect(field.getAttribute('aria-describedby')!.split(' ')).toContain('set-engine-min_request_interval_s-bound');
		info.focus();
		await user.keyboard('{Enter}');
		expect(info).toHaveAttribute('aria-expanded', 'true');
		expect(help).toHaveTextContent('Применяется сразу');
		await user.click(info);
		expect(help).not.toHaveTextContent('Применяется сразу');

		// Длинное первое предложение под подписью не показывается — только по ⓘ (полностью).
		await openGroup(user, 'Дела и прокачка');
		const weight = card('Дела').querySelector('[data-path="strategy.weight_xp"]') as HTMLElement;
		const long = within(weight).getByText(/^Оценка дела = /);
		expect(long).toHaveClass('hidden');
		await user.click(within(weight).getByRole('button', { name: 'Описание: Вес опыта' }));
		expect(long).not.toHaveClass('hidden');
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

	it('раскладка: карточки колонками без выравнивания по строкам, значение рядом с подписью', async () => {
		const user = userEvent.setup();
		await view();
		const flow = card('Дела').parentElement!;
		expect(flow).toHaveClass('columns-[30rem]', 'gap-x-3.5', '*:break-inside-avoid', '*:mb-3.5');
		expect(flow).not.toHaveClass('grid');
		const weight = card('Дела').querySelector('[data-path="strategy.weight_xp"]') as HTMLElement;
		expect(card('Дела')).toHaveClass('@container');
		expect(weight).toHaveClass('grid-cols-[minmax(0,1fr)_auto]', '@xl:grid-cols-[minmax(0,24rem)_auto]', '@xl:justify-start', 'py-1.5');
		expect(within(weight).getByRole('spinbutton', { name: 'Вес опыта' })).toHaveClass('w-28');

		await user.type(screen.getByRole('searchbox', { name: 'Поиск настройки' }), 'аптеч');
		const found = screen.getByRole('region', { name: 'Найденные настройки' });
		expect(card('Метро', found).parentElement).toHaveClass('columns-[30rem]', '*:break-inside-avoid');
	});

	it('id чата — числом пошире (w-40), чтобы -100… помещалось целиком', async () => {
		const user = userEvent.setup();
		await view();
		await openGroup(user, 'Мандарины и чаты');
		const row = card('Чаты').querySelector('[data-path="chats.game_chat_id"]') as HTMLElement;
		const input = within(row).getByRole('spinbutton');
		expect(input).toHaveClass('w-40');
		expect(input).not.toHaveClass('w-28');
	});

	it('поиск: результаты — карточками механик («аптеч» → поле сундука в «Метро»)', async () => {
		const user = userEvent.setup();
		await view();
		await user.type(screen.getByRole('searchbox', { name: 'Поиск настройки' }), 'аптеч');
		const found = screen.getByRole('region', { name: 'Найденные настройки' });
		const metro = card('Метро', found);
		expect(metro.querySelector('[data-path="metro.chest_min_packs"]')).not.toBeNull();
		// Только совпавшие поля: у «Бафов на входе» в подписи и описании аптечек нет.
		expect(metro.querySelector('[data-path="metro.min_budget_min"]')).toBeNull();
		expect(within(metro).getByRole('switch', { name: 'Метро' })).toBeInTheDocument();
		// Группа не выбрана, пока идёт поиск.
		expect(within(groups()).queryByRole('button', { current: true })).toBeNull();
	});

	it('поиск по флагу механики находит карточку и без полей', async () => {
		const user = userEvent.setup();
		await view();
		await user.type(screen.getByRole('searchbox', { name: 'Поиск настройки' }), 'ограблен');
		const found = screen.getByRole('region', { name: 'Найденные настройки' });
		const robbery = card('Защита от ограбления', found);
		expect(within(robbery).getByRole('switch', { name: 'Защита от ограбления' })).toBeInTheDocument();
		expect(robbery.querySelector('[data-path]')).toBeNull();
		// Поля «Движка» нашлись по описанию («при ограблении») — карточкой «Движок» со своими полями.
		expect(within(found).getAllByRole('region').map((r) => r.dataset.card)).toEqual(['robbery_defense', 'engine']);
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

	it('выбор группы сбрасывает поиск', async () => {
		const user = userEvent.setup();
		await view();
		const search = screen.getByRole('searchbox', { name: 'Поиск настройки' });
		await user.type(search, 'аптеч');
		await openGroup(user, 'Еда и сон');
		expect(search).toHaveValue('');
		expect(screen.getByRole('region', { name: 'Еда и сон' })).toBeInTheDocument();
		expect(within(groups()).getByRole('button', { name: 'Еда и сон' })).toHaveAttribute('aria-current', 'true');
	});

	it('группа — из адреса (?tab=)', async () => {
		page.url = new URL('http://app.invalid/a/1/settings?tab=battle');
		await view();
		expect(screen.getByRole('region', { name: 'Битва и деньги' })).toBeInTheDocument();
		expect(within(groups()).getByRole('button', { name: 'Битва и деньги' })).toHaveAttribute('aria-current', 'true');
	});

	it('неизвестная группа в адресе — первая', async () => {
		page.url = new URL('http://app.invalid/a/1/settings?tab=nope');
		await view();
		expect(screen.getByRole('region', { name: 'Дела и прокачка' })).toBeInTheDocument();
	});

	it('выбор группы пишет её в адрес без новой записи истории, прочий query остаётся', async () => {
		page.url = new URL('http://app.invalid/a/1/settings?x=1');
		const user = userEvent.setup();
		await view();
		await openGroup(user, 'Еда и сон');
		expect(goto).toHaveBeenCalledWith('/a/1/settings?x=1&tab=food', { replaceState: true, noScroll: true, keepFocus: true });
		expect(screen.getByRole('region', { name: 'Еда и сон' })).toBeInTheDocument();
	});

	async function openHistory(user: ReturnType<typeof userEvent.setup>) {
		expect(screen.queryByRole('list', { name: 'Версии настроек' })).toBeNull();
		await user.click(screen.getByRole('button', { name: 'История' }));
		const drawer = screen.getByRole('dialog', { name: 'История' });
		return { drawer, list: await within(drawer).findByRole('list', { name: 'Версии настроек' }) };
	}

	it('история — в выезжающей панели по кнопке в шапке: список версий и полный diff по клику', async () => {
		const user = userEvent.setup();
		await view();
		const { drawer, list } = await openHistory(user);
		const v8 = await within(list).findByRole('button', { name: /v8 / });
		expect(v8).toHaveTextContent(
			'Ежедневные задания · Включено → вкл; Дела · Разрешённые дела → harvest, job, learn, dconv, walk, confa'
		);
		await user.click(v8);
		expect(v8).toHaveAttribute('aria-expanded', 'true');
		expect(within(list).getByLabelText('Изменения версии 8')).toHaveTextContent('Ежедневные задания · Включено выкл → вкл');
		expect(within(list).getByLabelText('Изменения версии 8')).not.toHaveTextContent('features.daily_tasks');
		await user.click(within(drawer).getByRole('button', { name: 'Закрыть' }));
		expect(screen.queryByRole('dialog', { name: 'История' })).toBeNull();
	});

	it('история: «вернуть» кладёт прежнее значение в черновик, сохранение — как обычно', async () => {
		const user = userEvent.setup();
		const { editor } = await view();
		const { list } = await openHistory(user);
		await user.click(await within(list).findByRole('button', { name: /v13 / }));
		await user.click(within(list).getByRole('button', { name: 'Вернуть «Лотерея · Включено»: выкл' }));
		expect(editor.value(['features', 'lottery'])).toBe(false);
		const bar = screen.getByRole('region', { name: 'Несохранённые изменения' });
		expect(bar).toHaveTextContent('Лотерея · Включено: вкл → выкл');
		// Значение уже в черновике — второй раз вернуть нечего.
		expect(within(list).queryByRole('button', { name: /Вернуть «Лотерея · Включено»/ })).toBeNull();
		// Паузу меняют кнопки на главной — вернуть её из истории нельзя.
		await user.click(within(list).getByRole('button', { name: /v12 / }));
		expect(within(list).getByLabelText('Изменения версии 12')).toHaveTextContent('Движок · Пауза планировщика вкл → выкл');
		expect(within(within(list).getByLabelText('Изменения версии 12')).queryByRole('button')).toBeNull();
	});

	it('чужая версия из SSE перечитывает и открытую историю', async () => {
		const user = userEvent.setup();
		let version = 13;
		const { fetch, editor } = await view(undefined, () => ({ ...settings, version }));
		const history = () => fetch.calls.filter((c) => c.url.startsWith('/api/v1/accounts/1/settings/history')).length;
		// Закрытая панель историю не грузит.
		expect(history()).toBe(0);
		await openHistory(user);
		await vi.waitFor(() => expect(history()).toBe(1));
		version = 14;
		editor.onEvent({ type: 'settings', id: 'e:1', data: { version: 14, mode: 'live', paused: false, killed: false } });
		await vi.waitFor(() => expect(editor.version).toBe(14));
		await vi.waitFor(() => expect(history()).toBe(2));
	});

	it('граница видна у поля и ошибка 422 привязана к нему', async () => {
		const user = userEvent.setup();
		toasts.items = [];
		const { fetch } = await view(() =>
			json(
				{ detail: 'setting_out_of_bounds', path: 'engine.min_request_interval_s', bound: 'min', limit: 1.6 },
				422
			)
		);
		await openGroup(user, 'Дополнительно');
		const engine = card('Движок');
		expect(engine.querySelector('#set-engine-min_request_interval_s-bound')).toHaveTextContent('не меньше 1.6');
		expect(engine.querySelector('#set-engine-action_ttl_s-bound')).toHaveTextContent('не больше 600');
		// У поля без границы сервера подсказки нет.
		expect(engine.querySelector('#set-engine-default_expect_timeout_s-bound')).toBeNull();

		const interval = engine.querySelector('[data-path="engine.min_request_interval_s"]') as HTMLElement;
		const input = within(interval).getByRole('spinbutton', { name: 'Пауза между запросами, с' });
		await user.clear(input);
		await user.type(input, '0.5');
		await user.click(screen.getByRole('button', { name: 'Сохранить' }));
		expect(await within(interval).findByRole('alert')).toHaveTextContent(
			'Значение настройки выходит за границы: не меньше 1.6'
		);
		await vi.waitFor(() =>
			expect(toasts.items.map((t) => t.text)).toEqual(['Сервер не принял значения — поля подсвечены'])
		);
		const patch = fetch.calls.find((c) => c.method === 'PATCH')!;
		expect(JSON.parse(patch.body).changes).toEqual({ engine: { min_request_interval_s: 0.5 } });
		// Правки на месте: после ошибки можно исправить значение и сохранить снова.
		expect(screen.getByRole('region', { name: 'Несохранённые изменения' })).toBeInTheDocument();
	});

	it('422 без подходящего поля — сообщение с текстом ошибки, а не «поля подсвечены»', async () => {
		const user = userEvent.setup();
		toasts.items = [];
		await view(() =>
			json({ detail: [{ loc: ['body', 'changes', 'features'], msg: 'Value error, bad combo', type: 'value_error' }] }, 422)
		);
		await openGroup(user, 'Прочее');
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
		await openGroup(user, 'Прочее');
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
		await openGroup(user, 'Прочее');
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
