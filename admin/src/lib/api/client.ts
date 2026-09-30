import createClient, { type Middleware } from 'openapi-fetch';
import { ApiFailure, CSRF_MISMATCH, normalizeError } from './errors';
import type { paths } from './schema';

/** Единственное место базового адреса API: тот же origin, что у админки (пути схемы уже
 * начинаются с `/api/v1`; пути аккаунта — в `./account`). */
export const API_ORIGIN: string = typeof location === 'undefined' ? '' : location.origin;

export interface SessionHooks {
	/** CSRF-токен текущей сессии (только в памяти вкладки). */
	csrf(): string | null;
	/** Перечитать `/auth/me`; null — сессии нет. */
	refreshCsrf(): Promise<string | null>;
	/** 401: сессия закончилась. */
	unauthorized(): void;
}

const SAFE = new Set(['GET', 'HEAD', 'OPTIONS']);
// 401 входа и `/auth/me` — ответ по существу, а не истёкшая сессия.
const OWN_401 = new Set(['/api/v1/auth/login', '/api/v1/auth/me']);

async function isCsrfMismatch(response: Response): Promise<boolean> {
	try {
		const body: unknown = await response.clone().json();
		return (body as { detail?: unknown } | null)?.detail === CSRF_MISMATCH;
	} catch {
		return false;
	}
}

/** Сессия в запросах клиента: CSRF на изменяющих запросах, 401 → выход, 403 CSRF → перечитать
 * токен и повторить один раз. Общая для глобального клиента и клиентов аккаунтов. */
export function sessionMiddleware(hooks: SessionHooks, fetchImpl: typeof fetch): Middleware {
	const copies = new Map<string, Request>();
	return {
		onRequest({ request, id }) {
			if (SAFE.has(request.method)) return request;
			const token = hooks.csrf();
			if (token) request.headers.set('X-CSRF-Token', token);
			// Тело уйдёт в fetch: для повтора нужна копия до отправки.
			copies.set(id, request.clone());
			return request;
		},
		async onResponse({ response, id, schemaPath }) {
			const copy = copies.get(id);
			copies.delete(id);
			if (response.status === 401) {
				if (!OWN_401.has(schemaPath)) hooks.unauthorized();
				return response;
			}
			if (response.status !== 403 || copy === undefined || !(await isCsrfMismatch(response))) {
				return response;
			}
			const token = await hooks.refreshCsrf();
			if (!token) return response;
			copy.headers.set('X-CSRF-Token', token);
			const again = await fetchImpl(copy);
			if (again.status === 401) hooks.unauthorized();
			return again;
		},
		onError({ id }) {
			copies.delete(id);
		}
	};
}

export const defaultFetch: typeof fetch = (r) => fetch(r);

/** Глобальный клиент API: вход, пароль, каталог сценариев. */
export function createApi(hooks: SessionHooks, fetchImpl: typeof fetch = defaultFetch) {
	const client = createClient<paths>({
		baseUrl: API_ORIGIN,
		fetch: fetchImpl,
		credentials: 'same-origin'
	});
	client.use(sessionMiddleware(hooks, fetchImpl));
	return client;
}

export type Api = ReturnType<typeof createApi>;

interface Result<T> {
	data?: T;
	error?: unknown;
	response: Response;
}

/** Данные успешного ответа или `ApiFailure` с разобранной ошибкой (сеть — `network`). */
export async function call<T>(request: Promise<Result<T>>): Promise<T> {
	let result: Result<T>;
	try {
		result = await request;
	} catch (e) {
		throw new ApiFailure({ kind: 'network', status: 0, message: String(e) });
	}
	if (result.response.ok) return result.data as T;
	throw new ApiFailure(normalizeError(result.response.status, result.error, result.response.headers));
}
