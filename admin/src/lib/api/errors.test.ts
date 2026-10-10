import { describe, expect, it } from 'vitest';
import { errorText, normalizeError } from './errors';

describe('normalizeError', () => {
	it('строковые коды', () => {
		expect(normalizeError(401, { detail: 'not authenticated' })).toEqual({
			kind: 'unauthorized',
			status: 401
		});
		expect(normalizeError(403, { detail: 'csrf token mismatch' }).kind).toBe('csrf');
		expect(normalizeError(403, { detail: 'donate' })).toEqual({
			kind: 'forbidden',
			status: 403,
			code: 'donate'
		});
		expect(normalizeError(503, { detail: 'engine not running' }).kind).toBe('engine_down');
		expect(normalizeError(503, { detail: 'engine_starting' }).kind).toBe('engine_down');
		expect(errorText(normalizeError(503, { detail: 'engine not running' }))).toBe('Движок недоступен');
		expect(normalizeError(404, { detail: 'account not found' })).toEqual({
			kind: 'not_found',
			status: 404,
			code: 'account not found'
		});
		expect(normalizeError(503, { detail: 'store_failed' }).kind).toBe('store_failed');
		expect(normalizeError(404, { detail: 'decision not found' })).toMatchObject({
			kind: 'not_found',
			code: 'decision not found'
		});
		expect(normalizeError(422, { detail: 'invalid params: hours' })).toEqual({
			kind: 'invalid',
			status: 422,
			code: 'invalid params: hours'
		});
	});

	it('409 команды — confirm_required в оболочке detail', () => {
		const detail = {
			code: 'confirm_required',
			reason: 'missing',
			confirm_token: '1790.abc',
			expires_at: '2026-09-27T20:00:00Z',
			state_version: 638,
			command_class: 'risky'
		};
		const err = normalizeError(409, { detail });
		expect(err).toEqual({ kind: 'confirm', status: 409, confirm: detail });
	});

	it('409 настроек — version_conflict', () => {
		expect(normalizeError(409, { detail: { code: 'version_conflict', version: 14 } })).toEqual({
			kind: 'version_conflict',
			status: 409,
			version: 14
		});
		expect(normalizeError(409, { detail: 'lock_lost' })).toEqual({
			kind: 'conflict',
			status: 409,
			code: 'lock_lost'
		});
	});

	it('422 — список ошибок валидации', () => {
		const err = normalizeError(422, {
			detail: [
				{ loc: ['body', 'changes', 'sleep', 'duration_h'], msg: 'too big', type: 'less_than_equal' }
			]
		});
		expect(err).toEqual({
			kind: 'validation',
			status: 422,
			issues: [
				{ loc: ['body', 'changes', 'sleep', 'duration_h'], msg: 'too big', type: 'less_than_equal' }
			]
		});
		expect(errorText(err)).toBe('changes.sleep.duration_h: too big');
	});

	it('422 — свой чат в настройках, с полями', () => {
		const err = normalizeError(422, { detail: 'chat_is_self', fields: ['chats.game_chat_id'] });
		expect(err).toEqual({ kind: 'invalid', status: 422, code: 'chat_is_self', fields: ['chats.game_chat_id'] });
		expect(errorText(err)).toBe(
			'Указан сам пользователь Telegram этого аккаунта — его «Избранное» бот не читает: chats.game_chat_id'
		);
	});

	it('429 с Retry-After', () => {
		const err = normalizeError(429, { detail: 'flood_wait' }, new Headers({ 'Retry-After': '42' }));
		expect(err).toEqual({ kind: 'rate_limited', status: 429, code: 'flood_wait', retryAfter: 42 });
		expect(errorText(err)).toBe('Слишком часто — подождите 42 с');
	});

	it('429 лимита кодов входа — ожидание в минутах', () => {
		const headers = new Headers({ 'Retry-After': '3541' });
		expect(errorText(normalizeError(429, { detail: 'tg_code_rate_limited' }, headers))).toBe(
			'Слишком много запросов кода входа — следующий через 60 мин'
		);
		expect(
			errorText(normalizeError(429, { detail: 'tg_code_rate_limited' }, new Headers({ 'Retry-After': '9' })))
		).toBe('Слишком много запросов кода входа — следующий через 9 с');
	});

	it('тело не JSON (страница прокси)', () => {
		expect(normalizeError(502, null)).toEqual({ kind: 'unavailable', status: 502, code: '' });
		expect(normalizeError(500, 'oops')).toEqual({ kind: 'http', status: 500, code: '' });
		expect(errorText(normalizeError(500, null))).toBe('Ошибка 500');
	});

	it('новые коды ошибок дают русский текст', () => {
		const cases: [number, string, string][] = [
			[404, 'invite_not_found', 'Приглашение не найдено'],
			[410, 'invite_gone', 'Приглашение уже использовано, отозвано или истекло'],
			[409, 'login_taken', 'Логин уже занят'],
			[403, 'invalid_code', 'Неверный код'],
			[403, 'invalid_password', 'Неверный пароль'],
			[409, 'limit_reached', 'Достигнут лимит аккаунтов'],
			[409, 'server_full', 'На сервере нет свободных мест для аккаунтов'],
			[403, 'blocked_by_owner', 'Аккаунт заблокирован владельцем'],
			[422, 'setting_out_of_bounds', 'Значение настройки выходит за границы'],
			[409, 'tg_logged_in', 'Вход в Telegram уже выполнен'],
			[409, 'last_owner', 'Нельзя изменить или удалить последнего владельца'],
			[422, 'confirm_login_mismatch', 'Логин для подтверждения введён неверно'],
			[422, 'reason_required', 'Укажите причину блокировки'],
			[429, 'too_many_streams', 'Слишком много активных подключений'],
			[422, 'invalid_tg_app', 'Неверные api_id или api_hash приложения Telegram'],
			[503, 'secret_key_unavailable', 'Ключ шифрования сервера недоступен — попробуйте позже'],
			[409, 'scenario_not_manual', 'Этот сценарий запускает только бот'],
			[409, 'manual_queue_full', 'Очередь ручных запусков полна — дождитесь, пока пройдут поставленные']
		];
		for (const [status, code, text] of cases) {
			const err = normalizeError(status, { detail: code });
			expect(errorText(err), code).toBe(text);
		}
	});

	it('422 setting_out_of_bounds несёт путь и границу', () => {
		const err = normalizeError(422, {
			detail: 'setting_out_of_bounds',
			path: 'engine.min_request_interval_s',
			bound: 'min',
			limit: 1.6
		});
		expect(err).toEqual({
			kind: 'out_of_bounds',
			status: 422,
			path: 'engine.min_request_interval_s',
			bound: 'min',
			limit: 1.6
		});
		expect(errorText(err)).toBe('Значение настройки выходит за границы: не меньше 1.6');
		// Без пути и границы (неожиданная оболочка) — прежний текст по коду.
		expect(errorText(normalizeError(422, { detail: 'setting_out_of_bounds' }))).toBe(
			'Значение настройки выходит за границы'
		);
	});

	it('422 по полю login показывает русское описание требований', () => {
		const err = normalizeError(422, {
			detail: [
				{
					loc: ['body', 'login'],
					msg: "String should match pattern '^[A-Za-z0-9_.-]{3,64}$'",
					type: 'string_pattern_mismatch'
				}
			]
		});
		expect(errorText(err)).toBe('Логин: латиница, цифры, точка, дефис, подчёркивание; 3–64 символа');
	});
});

