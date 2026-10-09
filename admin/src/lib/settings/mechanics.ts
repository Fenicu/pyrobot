import { settingLabel } from './labels';
import { editable, leaves, pathKey, type Field, type Section } from './schema';

/** Механика в настройках: включатель `features.<x>` в заголовке и её параметры (листья схемы). */
export interface MechanicCard {
	id: string;
	title: string;
	/** Значок как в игре. */
	icon: string;
	/** Флаг механики — включатель в заголовке карточки; нет — карточка без включателя. */
	feature?: string;
	/** Пути листьев (`metro.buffs`) в порядке показа, без `feature`. */
	paths: string[];
}

export interface SettingsGroup {
	id: string;
	title: string;
	/** «Дополнительно»: то, что ставят один раз. */
	advanced?: boolean;
	cards: MechanicCard[];
}

/** Карточка «Прочие настройки»: поля, которых нет в таблице (сервер новее клиента). */
export const OTHER_CARD_ID = 'other';

/** Группа → карточка → пути. Каждый редактируемый лист схемы — ровно в одной карточке
 * (проверяет `mechanics.test.ts` по `settings.schema.json`). */
export const GROUPS: SettingsGroup[] = [
	{
		id: 'deeds',
		title: 'Дела и прокачка',
		cards: [
			{
				id: 'deeds',
				title: 'Дела',
				icon: '⛏',
				feature: 'features.deeds',
				paths: [
					'strategy.focus',
					'strategy.deeds',
					'strategy.weight_xp',
					'strategy.weight_money',
					'strategy.weight_resources',
					'strategy.exp_scale',
					'strategy.money_scale',
					'strategy.resource_scale'
				]
			},
			{ id: 'books', title: 'Книги', icon: '📒', feature: 'features.books', paths: [] },
			{ id: 'startup', title: 'Прокачка стартапа', icon: '🔮', feature: 'features.startup', paths: [] },
			{
				id: 'levelup',
				title: 'Прокачка навыков',
				icon: '🔨',
				feature: 'features.levelup',
				paths: ['levelup.policy']
			}
		]
	},
	{
		id: 'food',
		title: 'Еда и сон',
		cards: [
			{
				id: 'fastfood',
				title: 'Фастфуд',
				icon: '🍔',
				feature: 'features.fastfood',
				paths: ['food.order', 'food.banana_reserve']
			},
			{ id: 'smoothie', title: 'Смузи', icon: '🍹', feature: 'features.smoothie', paths: [] },
			{
				id: 'sleep',
				title: 'Сон',
				icon: '🛌',
				feature: 'features.sleep',
				paths: ['sleep.duration_h', 'sleep.lead_min', 'sleep.hotel_if_cash_after_reserve_ge']
			}
		]
	},
	{
		id: 'battle',
		title: 'Битва и деньги',
		cards: [
			{
				id: 'battle',
				title: 'Битва',
				icon: '⚔',
				feature: 'features.battle',
				paths: ['battle.target', 'battle.overrides']
			},
			{ id: 'factory', title: 'Фабрика', icon: '🏭', feature: 'features.factory', paths: [] },
			{ id: 'bulls', title: 'Биржевики', icon: '🐂', feature: 'features.bulls', paths: [] },
			{
				id: 'gorbushka',
				title: 'Горбушка',
				icon: '🏛',
				feature: 'features.gorbushka',
				// Запас 🔥 — в карточке той механики, под которую он держится (справка флагов Горбушки и метро).
				paths: ['strategy.reserve_ahead_min.gorbushka']
			},
			{
				id: 'stocks',
				title: 'Слив налички в акции',
				icon: '📈',
				feature: 'features.stocks_dump',
				paths: ['stocks.cash_floor', 'stocks.min_dump', 'stocks.sell_cap_margin', 'stocks.dump_lead_min']
			},
			{
				id: 'lottery',
				title: 'Лотерея',
				icon: '🤑',
				feature: 'features.lottery',
				paths: [
					'lottery.tickets.money',
					'lottery.tickets.knowledge',
					'lottery.tickets.raw',
					'lottery.tickets.details',
					'lottery.keep.money',
					'lottery.keep.knowledge',
					'lottery.keep.raw',
					'lottery.keep.details'
				]
			}
		]
	},
	{
		id: 'metro',
		title: 'Метро и поездки',
		cards: [
			{
				id: 'metro',
				title: 'Метро',
				icon: '🚇',
				feature: 'features.metro',
				paths: [
					'metro.min_budget_min',
					'metro.battle_margin_min',
					'metro.extra_margin_min',
					'strategy.reserve_ahead_min.metro',
					'metro.buffs',
					'metro.heal_at',
					'metro.heal_before_exit',
					'metro.chest_min_packs',
					'metro.npc_low_enabled',
					'metro.npc_high_enabled',
					'metro.npc_min_stamina'
				]
			},
			{ id: 'trips', title: 'Поездки', icon: '🚦', feature: 'features.trips', paths: ['trips.vehicles'] }
		]
	},
	{
		id: 'items',
		title: 'Подарки и предметы',
		cards: [
			{
				id: 'cards_containers',
				title: 'Карты и контейнеры',
				icon: '💳',
				feature: 'features.cards_containers',
				paths: []
			},
			{
				id: 'tangerine_gifts',
				title: 'Подарки за 🍊',
				icon: '🎁',
				feature: 'features.tangerine_gifts',
				paths: []
			},
			{
				id: 'gadgets',
				title: 'Гаджеты',
				icon: '🛒',
				feature: 'features.gadgets_buy',
				paths: ['gadgets.sets', 'gadgets.keep_money', 'gadgets.white_until']
			},
			{
				id: 'artifacts',
				title: 'Сбор артефакта',
				icon: '👾',
				paths: [
					'artifacts.book_low',
					'artifacts.book_high',
					'artifacts.fax',
					'artifacts.light',
					'artifacts.lottery_on_start'
				]
			}
		]
	},
	{
		id: 'daily',
		title: 'Задания дня',
		cards: [
			{
				id: 'daily_tasks',
				title: 'Ежедневные задания',
				icon: '📋',
				feature: 'features.daily_tasks',
				paths: ['daily.personal_order']
			},
			{ id: 'team_pick', title: 'Выбор командного задания', icon: '📋', feature: 'features.team_pick', paths: [] }
		]
	},
	{
		id: 'tangerine',
		title: 'Мандарины и чаты',
		cards: [
			{
				id: 'tangerine',
				title: 'Мандарин',
				icon: '🍊',
				feature: 'features.tangerine',
				paths: ['tangerine.interval_h', 'chats.tangerine_chat_id', 'chats.tangerine_reply_to']
			},
			{
				id: 'chats',
				title: 'Чаты',
				icon: '💬',
				// Каналы смузи и биржевиков — здесь, со всеми id чатов: их задают один раз при настройке аккаунта.
				paths: [
					'chats.game_chat_id',
					'chats.swinfo_chat_id',
					'chats.swinfo_user_id',
					'chats.smoothie_channel_id',
					'chats.bulls_invite_chat_id',
					'chats.team_chat_id'
				]
			}
		]
	},
	{
		id: 'misc',
		title: 'Прочее',
		cards: [
			{
				id: 'robbery_defense',
				title: 'Защита от ограбления',
				icon: '🥷',
				feature: 'features.robbery_defense',
				paths: []
			},
			{ id: 'pet_feast', title: 'Пир пета', icon: '🐾', feature: 'features.pet_feast', paths: [] },
			{ id: 'paid_info', title: 'Платная информация', icon: 'ℹ️', feature: 'features.paid_info', paths: [] },
			{ id: 'seasonal', title: 'Сезонные ивенты', icon: '🎄', feature: 'features.seasonal', paths: [] },
			{ id: 'casino', title: 'Казино', icon: '🎰', feature: 'features.casino', paths: [] },
			{ id: 'arena', title: 'Арена', icon: '🏟', feature: 'features.arena', paths: [] }
		]
	},
	{
		id: 'advanced',
		title: 'Дополнительно',
		advanced: true,
		cards: [
			{
				id: 'engine',
				title: 'Движок',
				icon: '🛠',
				paths: [
					'engine.mode',
					'engine.min_request_interval_s',
					'engine.antiflood_retry_max',
					'engine.antiflood_pause_s',
					'engine.action_ttl_s',
					'engine.default_expect_timeout_s',
					'engine.click_answer_timeout_s',
					'engine.recovered_react_max_age_min',
					'engine.refresh_min_interval_s',
					'engine.state_stale_after_min',
					'engine.urgent_while_paused',
					'engine.manual_while_paused'
				]
			},
			{ id: OTHER_CARD_ID, title: 'Прочие настройки', icon: '🗂', paths: [] }
		]
	}
];

const index = new Map<string, { group: SettingsGroup; card: MechanicCard }>();
for (const group of GROUPS) {
	for (const card of group.cards) {
		for (const path of [...(card.feature ? [card.feature] : []), ...card.paths]) index.set(path, { group, card });
	}
}
const features = new Set(GROUPS.flatMap((g) => g.cards.flatMap((c) => (c.feature ? [c.feature] : []))));

/** Карточка поля или включателя по пути листа; нет в таблице — null. */
export function cardOf(path: string): { group: SettingsGroup; card: MechanicCard } | null {
	return index.get(path) ?? null;
}

/** Редактируемые листья по карточкам в порядке `paths`; включатели — не поля (они в заголовке),
 * неизвестные — в `OTHER_CARD_ID` в порядке схемы. Ключи — все карточки `GROUPS`. */
export function placeFields(sections: Section[]): Map<string, Field[]> {
	const out = new Map<string, Field[]>(GROUPS.flatMap((g) => g.cards.map((c) => [c.id, [] as Field[]])));
	for (const field of sections.flatMap((s) => leaves(editable(s.fields)))) {
		const key = pathKey(field.path);
		if (features.has(key)) continue;
		out.get(cardOf(key)?.card.id ?? OTHER_CARD_ID)!.push(field);
	}
	for (const group of GROUPS) {
		for (const card of group.cards) {
			if (card.id === OTHER_CARD_ID) continue;
			out.get(card.id)!.sort((a, b) => card.paths.indexOf(pathKey(a.path)) - card.paths.indexOf(pathKey(b.path)));
		}
	}
	return out;
}

/** Подпись изменения для панели «Сохранить»: «Метро · Окно запаса»; включатель — «Метро · Включено»;
 * поля нет в таблице — запасные подписи (раздел схемы и название поля). */
export function changeLabel(
	path: string,
	fallbackSection: string,
	fallbackLabel: string
): { section: string; label: string } {
	const hit = cardOf(path);
	if (!hit) return { section: fallbackSection, label: fallbackLabel };
	if (hit.card.feature === path) return { section: hit.card.title, label: 'Включено' };
	return { section: hit.card.title, label: settingLabel(path, fallbackLabel) };
}
