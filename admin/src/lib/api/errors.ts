import type { ConfirmRequired } from './types';

/** Ошибка API в едином виде: по ней UI решает, что показать и что делать дальше. */
export type ApiError =
	| { kind: 'unauthorized'; status: 401 }
	| { kind: 'csrf'; status: 403 }
	| { kind: 'forbidden'; status: 403; code: string }
	| { kind: 'confirm'; status: 409; confirm: ConfirmRequired }
	| { kind: 'version_conflict'; status: 409; version: number }
	| { kind: 'conflict'; status: 409; code: string }
	| { kind: 'validation'; status: 422; issues: ValidationIssue[] }
	| { kind: 'invalid'; status: 422; code: string; fields?: string[] }
	| { kind: 'not_found'; status: 404; code: string }
	| { kind: 'rate_limited'; status: 429; code: string; retryAfter: number | null }
	| { kind: 'engine_down'; status: 503; code: string }
	| { kind: 'store_failed'; status: 503 }
	| { kind: 'unavailable'; status: number; code: string }
	| { kind: 'network'; status: 0; message: string }
	| { kind: 'http'; status: number; code: string };

export interface ValidationIssue {
	loc: (string | number)[];
	msg: string;
	type: string;
}

export const CSRF_MISMATCH = 'csrf token mismatch';
// Правка настроек: поле `chats.*` — сам пользователь Telegram аккаунта (`fields` — какие).
export const CHAT_IS_SELF = 'chat_is_self';
// Запросов кода входа в Telegram больше лимита хоста или аккаунта.
export const TG_CODE_RATE_LIMITED = 'tg_code_rate_limited';
// Движок аккаунта не запущен, ещё регистрируется или без цикла планировщика.
const ENGINE_DOWN = new Set(['engine not running', 'engine_starting', 'planner not started']);

function isRecord(v: unknown): v is Record<string, unknown> {
	return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function issues(detail: unknown[]): ValidationIssue[] {
	return detail.filter(isRecord).map((d) => ({
		loc: Array.isArray(d.loc) ? (d.loc as (string | number)[]) : [],
		msg: String(d.msg ?? ''),
		type: String(d.type ?? '')
	}));
}

/** Разбор ответа с ошибкой по реальным оболочкам FastAPI: строковый код, объект с `code`,
 * список ошибок валидации. */
export function normalizeError(status: number, body: unknown, headers?: Headers): ApiError {
	const detail = isRecord(body) ? body.detail : undefined;
	const code = typeof detail === 'string' ? detail : '';
	if (status === 401) return { kind: 'unauthorized', status };
	if (status === 403) {
		return code === CSRF_MISMATCH ? { kind: 'csrf', status } : { kind: 'forbidden', status, code };
	}
	if (status === 409 && isRecord(detail)) {
		if (detail.code === 'confirm_required') {
			return { kind: 'confirm', status, confirm: detail as unknown as ConfirmRequired };
		}
		if (detail.code === 'version_conflict' && typeof detail.version === 'number') {
			return { kind: 'version_conflict', status, version: detail.version };
		}
	}
	if (status === 409) return { kind: 'conflict', status, code };
	if (status === 422) {
		if (Array.isArray(detail)) return { kind: 'validation', status, issues: issues(detail) };
		const fields = isRecord(body) && Array.isArray(body.fields) ? body.fields.map(String) : null;
		return fields ? { kind: 'invalid', status, code, fields } : { kind: 'invalid', status, code };
	}
	if (status === 404) return { kind: 'not_found', status, code };
	if (status === 429) {
		const raw = headers?.get('Retry-After');
		const retryAfter = raw !== null && raw !== undefined && /^\d+$/.test(raw) ? Number(raw) : null;
		return { kind: 'rate_limited', status, code, retryAfter };
	}
	if (status === 503 && code === 'store_failed') return { kind: 'store_failed', status };
	if (status === 503 && (ENGINE_DOWN.has(code) || code === '')) {
		return { kind: 'engine_down', status, code };
	}
	if (status === 502 || status === 503 || status === 504) return { kind: 'unavailable', status, code };
	return { kind: 'http', status, code };
}

const CODE_TEXT: Record<string, string> = {
	forbidden: 'Команда запрещена: такие не отправляются никогда',
	donate: 'Донат-команды не отправляются',
	'invalid current password': 'Неверный текущий пароль',
	'invalid credentials': 'Неверный логин или пароль',
	lock_lost: 'Движок потерял аренду аккаунта и перезапускается — повторите позже',
	'idempotency_key reused': 'Этот ключ уже использован с другими параметрами',
	'unknown scenario': 'Нет такого сценария',
	'account not found': 'Аккаунт не найден',
	account_deleting: 'Аккаунт удаляется',
	name_taken: 'Аккаунт с таким именем уже есть',
	capacity_reached: 'Достигнут предел включённых аккаунтов — выключите другой аккаунт',
	confirm_name_mismatch: 'Имя аккаунта введено неверно',
	[CHAT_IS_SELF]: 'Указан сам пользователь Telegram этого аккаунта — его «Избранное» бот не читает',
	tg_not_online: 'Telegram не в сети',
	dry_run: 'Запуск сбора — только в режиме live',
	artifact_max: 'Артефакт уже 100 уровня',
	run_in_progress: 'Сбор уже идёт или запускается',
	locked_until: 'Новый сбор пока нельзя: идут 10 суток прошлого',
	deeds_disabled: 'Дела выключены в «Функциях» — сбор не потратит 🔥. Включите дела и запустите снова',
	no_run: 'Сбора нет — нечего ставить на паузу, продолжать или отменять',
	not_collecting: 'В игре сбор не идёт',
	not_external: 'Этот сбор уже ведёт бот',
	invite_not_found: 'Приглашение не найдено',
	invite_gone: 'Приглашение уже использовано, отозвано или истекло',
	login_taken: 'Логин уже занят',
	invalid_code: 'Неверный код',
	invalid_password: 'Неверный пароль',
	limit_reached: 'Достигнут лимит аккаунтов',
	server_full: 'На сервере нет свободных мест для аккаунтов',
	blocked_by_owner: 'Аккаунт заблокирован владельцем',
	setting_out_of_bounds: 'Значение настройки выходит за границы',
	tg_logged_in: 'Вход в Telegram уже выполнен',
	last_owner: 'Нельзя изменить или удалить последнего владельца',
	confirm_login_mismatch: 'Логин для подтверждения введён неверно',
	reason_required: 'Укажите причину блокировки',
	too_many_streams: 'Слишком много активных подключений'
};

/** Ожидание для человека: секунды до минуты, дальше — минуты вверх. */
function waitText(seconds: number): string {
	return seconds < 60 ? `${seconds} с` : `${Math.ceil(seconds / 60)} мин`;
}

/** Текст ошибки для человека; код сервера — как есть, если перевода нет. */
export function errorText(err: ApiError): string {
	switch (err.kind) {
		case 'unauthorized':
			return 'Сессия закончилась — войдите снова';
		case 'csrf':
			return 'Сессия обновилась — повторите действие';
		case 'confirm':
			return 'Команда требует подтверждения';
		case 'version_conflict':
			return `Настройки уже изменены (версия ${err.version}) — перечитайте`;
		case 'validation':
			return err.issues.map((i) => `${i.loc.slice(1).join('.')}: ${i.msg}`).join('\n') || 'Ошибка проверки';
		case 'rate_limited':
			if (err.code === TG_CODE_RATE_LIMITED) {
				const text = 'Слишком много запросов кода входа';
				return err.retryAfter !== null ? `${text} — следующий через ${waitText(err.retryAfter)}` : text;
			}
			const known = CODE_TEXT[err.code];
			if (known !== undefined) return known;
			return err.retryAfter !== null
				? `Слишком часто — подождите ${err.retryAfter} с`
				: 'Слишком часто — подождите';
		case 'invalid':
			return err.fields?.length
				? `${CODE_TEXT[err.code] ?? err.code}: ${err.fields.join(', ')}`
				: (CODE_TEXT[err.code] ?? err.code);
		case 'engine_down':
			return 'Движок недоступен';
		case 'store_failed':
			return 'Запись не сохранилась — можно повторить';
		case 'network':
			return 'Нет связи с сервером';
		case 'unavailable':
			return err.status === 502 ? 'Telegram недоступен' : 'Сервис временно недоступен';
		default:
			return CODE_TEXT[err.code] ?? (err.code || `Ошибка ${err.status}`);
	}
}

/** Исключение с разобранной ошибкой API. */
export class ApiFailure extends Error {
	readonly error: ApiError;
	constructor(error: ApiError) {
		super(errorText(error));
		this.name = 'ApiFailure';
		this.error = error;
	}
}
