/** Тексты «Плана бота»: причины таймеров, вердикты кандидатов, сценарии — коды как в движке. */
import type { Outlook, PlanCandidate, PlanTimer, WakeKind } from '$lib/api/types';
import { fmtTime } from '$lib/util/format';
import { CURRENCY, PERSONAL_TASK, VEHICLE } from '$lib/util/game';

export interface WakeText {
	icon: string;
	text: string;
}

/** Причины пробуждения — исчерпывающе по `WakeKind`: новую причину без текста не даст компилятор. */
export const WAKE: Record<WakeKind, WakeText> = {
	busy: { icon: '⏳', text: 'Освободится' },
	cooldown: { icon: '⌛', text: 'Кончится отсрочка' },
	refresh: { icon: '🔄', text: 'Можно снова обновить экран' },
	sleep_window: { icon: '🛌', text: 'Сон' },
	sleep_allowed: { icon: '🛌', text: 'Игра разрешит лечь спать' },
	book_ready: { icon: '📒', text: 'Прочитать книгу' },
	card_ready: { icon: '💳', text: 'Подарочная карта' },
	fastfood_ready: { icon: '🍔', text: 'Фастфуд снова доступен' },
	prizebox_ready: { icon: '🎁', text: 'Призовая коробка' },
	gorbushka_next: { icon: '🏛', text: 'Горбушка: бой с продаваном' },
	gorbushka_comeback: { icon: '🏛', text: 'Горбушка снова открыта' },
	motivation: { icon: '🔥', text: '+1 мотивация' },
	battle: { icon: '⚔', text: 'Конец окна битвы — снова дела' },
	daily_midnight: { icon: '📋', text: 'Задания: пауза у полуночи кончится' },
	daily_reset: { icon: '📋', text: 'Новый день заданий — выбрать личное hard' },
	stocks_dump: { icon: '📈', text: 'Слив налички в акции перед битвой' },
	factory_open: { icon: '🏭', text: 'Запись на фабрику' },
	factory_report: { icon: '🏭', text: 'Отчёт о битве за фабрику' },
	tangerine_ready: { icon: '🍊', text: 'Мандарин: /gt' },
	tangerine_not_player: { icon: '🍊', text: 'Мандарин: пауза «не играет» кончится' },
	lottery_open: { icon: '🤑', text: 'Лотерея: купить билеты' },
	metro_kick: { icon: '🚇', text: 'Метро: игра выкинет перед битвой' },
	metro_ready: { icon: '🚇', text: 'Метро доступно' },
	metro_probe: { icon: '🚇', text: 'Метро: проверить выход после итога' },
	artifact_end: { icon: '👾', text: 'Сбор артефакта кончается' },
	trip_ready: { icon: '🚦', text: 'Можно ехать' },
	trip_result: { icon: '🚦', text: 'Итог поездки' },
	market_open: { icon: '📈', text: 'Откроется биржа' },
	gear_guard: { icon: '🛡', text: 'Кончится окно-запрет' }
};

/** Сценарии и псевдо-сценарии кандидатов (`state`, `deeds` — обновление ради занятости и дел). */
export const SCENARIO: Record<string, string> = {
	refresh: '🔄 обновить экран',
	fastfood: '🍔 фастфуд',
	levelup: '🔨 прокачка навыков',
	gorbushka: '🏛 Горбушка',
	sleep: '🛌 сон',
	battle_target: '⚔ цель битвы',
	stocks_dump: '📈 слив налички в акции',
	factory_signup: '🏭 запись на фабрику',
	factory_report: '🏭 отчёт о фабрике',
	bulls_join: '🐂 бой с биржевиками',
	tangerine: '🍊 мандарин',
	tangerine_gifts: '🎁 подарки за 🍊',
	smoothie: '🍹 смузи',
	metro: '🚇 метро',
	daily_refresh: '📋 перечитать задания',
	daily_pick: '📋 выбрать личное задание',
	team_pick: '📋 выбрать командное задание',
	lottery_buy: '🤑 билеты лотереи',
	book: '📒 книга',
	card: '💳 подарочная карта',
	prizebox: '🎁 призовая коробка',
	container_small: '🗳 малый контейнер',
	container_medium: '🗳 средний контейнер',
	artifact_start: '👾 запуск сбора артефакта',
	artifact: '👾 сбор артефакта',
	trip: '🚦 поездка',
	trips_refresh: '🚦 обновление экрана транспорта',
	gadget_buy: '🛒 покупка гаджета',
	gadget_wear_set: '🎽 надеть сет',
	gadget_upgrade: '🗜 заточка',
	'deed:harvest': '⛏ добыча',
	'deed:job': '💻 работа',
	'deed:learn': '📚 учёба',
	'deed:dconv': '⚙️→🔩 переработка',
	'deed:eat': '🍴 еда',
	'deed:walk': '🚶 прогулка',
	'deed:confa': '📚 конференция',
	'deed:rob': '🔫 грабёж',
	'deed:startup': '🖥 пилить стартап',
	state: '👤 занятость',
	deeds: '⛏ дела'
};

/** Подпись сценария; незнакомый — как есть. */
export function scenarioText(name: string): string {
	return SCENARIO[name] ?? name;
}

/** Дело без значка: «переработка». */
export function deedText(name: string): string {
	return scenarioText(name).replace(/^\S+\s/, '');
}

/** Дела с одним значком в игре (`app/engine/parsing/activities.py`: и «Учиться», и «Конфа» шлют
 * 📚) — короткая метка вместо голого значка, иначе счётчик «Основные дела» их не различает. */
const DEED_TAG: Record<string, string> = {
	'deed:learn': '📚уч',
	'deed:confa': '📚конф'
};

/** Метка дела для счётчика «Основные дела»: значок сценария, а где он не свой — короткая метка. */
export function deedTag(name: string): string {
	return DEED_TAG[name] ?? scenarioText(name).split(' ')[0]!;
}

export const SOURCE_TEXT: Record<string, string> = {
	profile: 'профиль',
	inventory: 'инвентарь',
	food: 'меню еды',
	gifts: 'подарки',
	gorbushka: 'Горбушку',
	daily: 'задания',
	artifacts: 'экран артефактов',
	trips: 'транспорт',
	upgrades: 'апгрейды',
	stocks: 'биржу',
	startup: 'экран стартапа'
};

/** Вердикты кандидатов (`Candidate.verdict`); `stale:<поле>` — отдельно. */
export const VERDICT: Record<string, string> = {
	chosen: 'выбрано',
	ok: 'тоже можно',
	busy: 'занят',
	eating: 'ест',
	no_motivation: 'нет 🔥',
	no_money: 'нет 💵',
	no_details: 'нет ⚙️',
	no_value: 'невыгодно',
	battle_window: 'скоро битва',
	sleep_deadline: 'не успеть до сна',
	factory_window: 'мешает записи на фабрику',
	uncertified: 'не сертифицирован',
	cooldown: 'отсрочка',
	rate_limited: 'рано перечитывать',
	not_feasible: 'не успеть до 24:00',
	no_hard_offer: 'нет hard-задания',
	no_hard_team_offer: 'нет командного hard-задания',
	cant_afford: 'не по карману',
	sleep_not_allowed: 'спать пока нельзя',
	market_closed: 'биржа закрыта',
	no_stock: 'нет подходящей акции',
	not_player: 'адресат не играет',
	in_metro: 'уже в метро',
	metro_unknown_screen: 'незнакомый экран метро',
	metro_stuck: 'выход из метро не подтверждён',
	reserved: '🔥 в запасе',
	no_team: 'не в команде',
	company_unknown: 'своя компания не распознана',
	artifact_run: 'идёт сбор артефакта',
	no_raw: 'нет 🔩',
	motivation_cap: '🔥 у максимума — сначала дело',
	trip_pending: 'ждёт итог прошлой поездки',
	bag_full: 'рюкзак полон',
	saving: 'копим на сет',
	no_upgrade: 'нечего улучшать',
	target_blocked: 'нужны 💍/💻 сета',
	dump_window: 'окно слива акций',
	gorbushka_meeting: 'встреча на Горбушке',
	upgrade_running: 'на слоте идёт заточка',
	shop_mismatch: 'витрина не сходится с каталогом',
	no_knowledge: 'нет 📚',
	startup_locked: 'стартапы — с 18🎚'
};

/** Поля состояния в вердикте `stale:<поле>`. */
const FIELD_TEXT: Record<string, string> = {
	busy: 'занятость',
	money: '💵',
	motivation: '🔥',
	stamina: '🔋',
	knowledge: '📚',
	raw: '🔩',
	details: '⚙️',
	battle_at: 'время битвы',
	sleep_deadline: 'дедлайн сна',
	books: 'книги',
	cards: 'карты',
	gorbushka: 'Горбушка',
	food_stock: 'запас еды',
	company: 'своя компания',
	team_tag: 'команда',
	artifact_collect: 'сбор артефакта',
	trips: 'транспорт',
	level: 'уровень',
	gadgets: 'гаджеты',
	bag: 'рюкзак',
	bag_cap: 'размер рюкзака',
	upgrades: 'запас улучшений',
	upgrade_info: 'экран апгрейдов',
	stock_holdings: 'портфель акций',
	stock_quotes: 'котировки',
	stock_limits: 'лимиты биржи',
	startup: 'стартап',
	tangerines: '🍊',
	tangerine_gifts: 'подарки за 🍊'
};

/** Отклонённый командный вариант главы: `team <тип> <N>🔥` или `team <тип> ?🔥`. */
const TEAM_OFFER = /^team (\S+) (\d+|\?)🔥$/;

export function verdictText(verdict: string): string {
	if (verdict.startsWith('stale:')) {
		const field = verdict.slice('stale:'.length);
		return `нужно обновить: ${FIELD_TEXT[field] ?? field}`;
	}
	const team = TEAM_OFFER.exec(verdict);
	if (team) {
		const [, type = '', fire] = team;
		const kind = PERSONAL_TASK[type] ?? type;
		return fire === '?' ? `${kind} — доход неизвестен` : `${kind} — ${fire}🔥`;
	}
	return VERDICT[verdict] ?? verdict;
}

export type Tone = 'ok' | 'warn' | 'muted';

export function verdictTone(verdict: string): Tone {
	if (verdict === 'chosen') return 'ok';
	if (verdict === 'ok' || verdict.startsWith('stale:') || verdict === 'cooldown' || verdict === 'reserved') return 'muted';
	if (TEAM_OFFER.test(verdict)) return 'muted';
	return 'warn';
}

/** Причины неготовности цикла (`loop.ready`). */
export const READY: Record<string, string> = {
	paused: 'пауза',
	killed: 'включён kill switch',
	spending_blocked: 'траты заблокированы до сверки',
	pipeline_unhealthy: 'конвейер сообщений нездоров',
	lock_lost: 'потеряна аренда аккаунта',
	tg_offline: 'Telegram не в сети',
	bulls_walk: 'встреча с биржевиком на прогулке'
};

export function readyText(reason: string): string {
	return READY[reason] ?? reason;
}

function tickets(value: number | 'max'): string {
	return value === 'max' ? 'max' : String(value);
}

/** Билеты лотереи по настройкам: «все — max» или по валютам. */
export function lotteryTickets(plan: Outlook): string {
	const entries = Object.entries(plan.hints.lottery_tickets);
	if (entries.length > 0 && entries.every(([, v]) => v === 'max')) return 'все — max';
	return entries.map(([c, v]) => `${CURRENCY[c] ?? c} ${tickets(v)}`).join(', ');
}

function sleepPlace(plan: Outlook): string {
	const place = plan.hints.sleep_place;
	return place === 'hotel' ? ' в отеле' : place === 'bridge' ? ' под мостом' : '';
}

function refreshKey(key: string | null): string {
	if (key === null) return '';
	return SOURCE_TEXT[key] ?? key;
}

/** Что именно откладывает кулдаун: `deed:job`, `refresh:profile`, `book`. */
function cooldownKey(key: string | null): string {
	if (key === null) return '';
	if (key.startsWith('refresh:')) return `обновить ${refreshKey(key.slice('refresh:'.length))}`;
	return deedText(key);
}

export interface TimerLine {
	icon: string;
	text: string;
	/** Подробность справа: цель битвы, место сна. */
	detail: string;
}

/** Строка «Дальше по времени». */
export function timerLine(t: PlanTimer, plan: Outlook): TimerLine {
	const base = WAKE[t.kind];
	let text = base.text;
	let detail = '';
	switch (t.kind) {
		case 'busy':
			text = plan.phase === 'asleep' ? 'Проснётся' : base.text;
			break;
		case 'cooldown':
			text = `${base.text}: ${cooldownKey(t.key)}`;
			break;
		case 'refresh':
			text = `${base.text}: ${refreshKey(t.key)}`;
			break;
		case 'sleep_window':
			text = `Сон ${plan.hints.sleep_hours} ч${sleepPlace(plan)}`;
			// Место сна — по деньгам; при устаревшей занятости таймеры — второй проход на последних.
			if (plan.hints.sleep_place !== null) detail = plan.basis ? 'по последним данным о деньгах' : 'на текущих деньгах';
			break;
		case 'lottery_open':
			text = `${base.text} (${lotteryTickets(plan)})`;
			break;
		case 'trip_ready':
			text = `${base.text}: ${VEHICLE[t.key ?? ''] ?? t.key}`;
			break;
		case 'battle':
		case 'stocks_dump':
		case 'metro_kick':
			detail = plan.hints.battle_target ? `цель ${plan.hints.battle_target}` : '';
			break;
	}
	return { icon: base.icon, text, detail };
}

/** Подробность действия: что именно сделает сценарий. */
export function actDetail(scenario: string, params: Record<string, unknown>, plan: Outlook): string {
	switch (scenario) {
		case 'refresh':
			return typeof params.source === 'string' ? refreshKey(params.source) : '';
		case 'fastfood':
			return typeof params.food === 'string' ? (CURRENCY[params.food] ?? params.food) : '';
		case 'battle_target':
			return typeof params.target === 'string' ? params.target : '';
		case 'lottery_buy':
			return lotteryTickets(plan);
		case 'sleep':
			return `${plan.hints.sleep_hours} ч${sleepPlace(plan)}`;
		case 'daily_pick':
		case 'team_pick': {
			const kind = typeof params.task === 'string' ? params.task.replace(/_\w+$/, '') : '';
			return PERSONAL_TASK[kind] ?? kind;
		}
		case 'gorbushka':
			return params.buy === true ? 'купить билет' : '';
		case 'metro':
			return typeof params.probe === 'string' ? `проверка выхода: /${params.probe}` : '';
		case 'trip':
			return typeof params.vehicle === 'string' ? (VEHICLE[params.vehicle] ?? params.vehicle) : '';
		default:
			return '';
	}
}

/** Под что держится запас 🔥 (`reserves`) — для отказа `reserved`. */
const RESERVE_FOR: Record<Outlook['reserves'][number]['kind'], string> = {
	gorbushka: 'Горбушку',
	metro: 'метро'
};

/** Запасы, из-за которых отказано: у метро — только запас Горбушки (свой вход он и тратит). */
function reservedFor(c: PlanCandidate, plan: Outlook): string {
	const held = plan.reserves.filter((r) => c.scenario !== 'metro' || r.kind !== 'metro');
	return held.length > 0 ? `под ${held.map((r) => RESERVE_FOR[r.kind]).join(' и ')}` : '';
}

/** Подробность отказа: когда он снимется, если это видно по таймерам; у запаса 🔥 — под что он. */
export function candidateDetail(c: PlanCandidate, plan: Outlook): string {
	const find = (kind: WakeKind, key: string | null = null) =>
		plan.wakeups.find((t) => t.kind === kind && t.key === key && !t.after_wake);
	let timer: PlanTimer | undefined;
	if (c.verdict === 'no_motivation') timer = find('motivation');
	else if (c.verdict === 'battle_window') timer = c.scenario.startsWith('gadget_') ? find('gear_guard') : find('battle');
	else if (c.verdict === 'gorbushka_meeting') timer = find('gear_guard');
	else if (c.verdict === 'market_closed') timer = find('market_open');
	else if (c.verdict === 'cooldown') {
		const source = c.params.source;
		timer = find('cooldown', c.scenario === 'refresh' ? `refresh:${String(source)}` : c.scenario);
	}
	else if (c.verdict === 'busy') timer = find('busy');
	else if (c.verdict === 'trip_pending') timer = find('trip_result');
	const today = typeof c.params.today === 'number' ? `сегодня ${c.params.today}` : '';
	const wait = timer ? `до ${fmtTime(timer.at)}` : '';
	const reserve = c.verdict === 'reserved' ? reservedFor(c, plan) : '';
	return [today, wait, reserve].filter(Boolean).join(', ');
}
