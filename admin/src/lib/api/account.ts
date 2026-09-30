import createClient from 'openapi-fetch';
import { API_ORIGIN, defaultFetch, sessionMiddleware, type SessionHooks } from './client';
import type { paths } from './schema';

const PREFIX = '/api/v1/accounts/{account_id}';
type Prefix = typeof PREFIX;

type WithoutAccount<Params> = Params extends { path: infer P }
	? keyof Omit<P, 'account_id'> extends never
		? Omit<Params, 'path'> & { path?: never }
		: Omit<Params, 'path'> & { path: Omit<P, 'account_id'> }
	: Params;

type AccountOperation<Op> = Op extends { parameters: infer Params }
	? Omit<Op, 'parameters'> & { parameters: WithoutAccount<Params> }
	: Op;

/** Пути аккаунта из схемы: префикс `/api/v1/accounts/{account_id}` срезан, `account_id` из
 * параметров пути убран — его подставляет клиент аккаунта. */
export type AccountPaths = {
	[K in keyof paths as K extends `${Prefix}${infer Rest}` ? Rest : never]: {
		[M in keyof paths[K]]: AccountOperation<paths[K][M]>;
	};
};

/** Клиент API аккаунта: те же сессия и CSRF, что у глобального, пути — относительно аккаунта
 * (`GET('/engine/status')`). */
export function createAccountApi(hooks: SessionHooks, accountId: number, fetchImpl: typeof fetch = defaultFetch) {
	const client = createClient<AccountPaths>({
		baseUrl: `${API_ORIGIN}/api/v1/accounts/${accountId}`,
		fetch: fetchImpl,
		credentials: 'same-origin'
	});
	client.use(sessionMiddleware(hooks, fetchImpl));
	return client;
}

export type AccountApi = ReturnType<typeof createAccountApi>;

/** Поток событий (SSE) движка аккаунта. */
export function eventsUrl(accountId: number): string {
	return `${API_ORIGIN}/api/v1/accounts/${accountId}/events`;
}
