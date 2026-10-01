/** Подписи игровых сущностей для экранов (коды — как в движке). */
import { fmtMoment } from './format';

export const ACTIVITY: Record<string, string> = {
	harvest: 'добыча',
	job: 'работа',
	learn: 'учёба',
	dconv: 'переработка',
	walk: 'прогулка',
	confa: 'конференция',
	rob: 'грабёж',
	gorbushka: 'Горбушка',
	sleep_hotel: 'сон в отеле',
	sleep_bridge: 'сон под мостом',
	bulls: 'бой с биржевиками',
	unknown: 'занят'
};

export function activityLabel(code: string): string {
	return ACTIVITY[code] ?? code;
}

/** Занятость из снимка: «работа до 21:34»; `until` прошёл по часам страницы — «… · уже свободен»
 * (дела начинает только бот, а снимок обновится со следующим профилем). */
export function busyText(busy: { activity: string; until: string }, now: Date): string {
	const text = `${activityLabel(busy.activity)} до ${fmtMoment(busy.until, now)}`;
	return Date.parse(busy.until) <= now.getTime() ? `${text} · уже свободен` : text;
}

/** Валюты лотереи и ресурсы: символы игры. */
export const CURRENCY: Record<string, string> = {
	money: '💵',
	knowledge: '📚',
	raw: '🔩',
	details: '⚙️',
	motivation: '🔥',
	stamina: '🔋',
	exp: '💡',
	tokens: '🕳',
	pizza: '🍕',
	burger: '🍔',
	hotdog: '🌭',
	banana: '🍌',
	upgrades_white: '⚪',
	upgrades_blue: '🔵'
};

export const PERSONAL_TASK: Record<string, string> = {
	convDets: 'переработка ⚙️',
	robPro: 'Продаваны',
	jobMoney: 'работа 💵',
	materials: 'сырьё 🔩',
	learnKnows: 'учёба 📚',
	walkMoney: 'прогулка 💵',
	confKnows: 'конфа 📚'
};

export const LEVEL: Record<string, string> = { easy: 'лёгкое', medium: 'среднее', hard: 'сложное' };

export const GORBUSHKA: Record<string, string> = {
	done: 'бои закончены',
	need_ticket: 'нужен билет',
	meeting: 'бой сейчас',
	waiting: 'ждёт боя'
};

export const SOURCE: Record<string, string> = {
	planner: 'план',
	scenario: 'сценарий',
	manual: 'ручное',
	urgent: 'срочное'
};

/** Состояние входа в Telegram (`TgStatusOut.state`). */
export const TG_STATE: Record<string, string> = {
	unauthorized: 'не выполнен вход',
	awaiting_code: 'ждёт код',
	awaiting_password: 'ждёт пароль 2FA',
	online: 'online',
	overload: 'перегрузка',
	error: 'ошибка',
	stopped: 'движок не запущен'
};

export function tgStateLabel(state: string): string {
	return TG_STATE[state] ?? state;
}

export const ACTION_STATUS: Record<string, string> = {
	intent: 'в очереди',
	sent: 'отправлено',
	confirmed: 'подтверждено',
	refused: 'отказ',
	suppressed: 'подавлено',
	outcome_unknown: 'исход неизвестен',
	rejected: 'отклонено'
};

/** Что за команда у действия: текст или кнопка; пересылка — «→ чат команды «название»,
 * сообщение #id» (название — из проверки чата Telegram, по нему видна опечатка в ID). */
export function actionCommand(
	kind: string,
	p: { text?: unknown; data?: unknown; message_id?: unknown; chat_title?: unknown }
): string {
	if (kind === 'forward') {
		const title = typeof p.chat_title === 'string' && p.chat_title ? ` «${p.chat_title}»` : '';
		return `→ чат команды${title}, сообщение #${p.message_id ?? '?'}`;
	}
	const command = p.text ?? p.data;
	return command === null || command === undefined ? '—' : String(command);
}

/** Тон значка статуса действия или запуска. */
export function statusTone(status: string): 'ok' | 'warn' | 'bad' | 'muted' {
	if (['confirmed', 'done', 'online'].includes(status)) return 'ok';
	if (['refused', 'rejected', 'failed', 'outcome_unknown', 'error', 'crashed'].includes(status)) return 'bad';
	if (['suppressed', 'stopped', 'interrupted', 'cancelled', 'nothing'].includes(status)) return 'warn';
	return 'muted';
}
