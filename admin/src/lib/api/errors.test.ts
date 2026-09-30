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

	it('429 с Retry-After', () => {
		const err = normalizeError(429, { detail: 'flood_wait' }, new Headers({ 'Retry-After': '42' }));
		expect(err).toEqual({ kind: 'rate_limited', status: 429, code: 'flood_wait', retryAfter: 42 });
		expect(errorText(err)).toBe('Слишком часто — подождите 42 с');
	});

	it('тело не JSON (страница прокси)', () => {
		expect(normalizeError(502, null)).toEqual({ kind: 'unavailable', status: 502, code: '' });
		expect(normalizeError(500, 'oops')).toEqual({ kind: 'http', status: 500, code: '' });
		expect(errorText(normalizeError(500, null))).toBe('Ошибка 500');
	});
});
