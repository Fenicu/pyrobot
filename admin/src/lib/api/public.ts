import createClient from 'openapi-fetch';
import { API_ORIGIN, call, defaultFetch } from './client';
import type { paths } from './schema';
import type { InviteAcceptOut, InvitePeekOut, MeOut, RecoverFinishIn } from './types';

export function createPublicClient(fetchImpl: typeof fetch = defaultFetch) {
	return createClient<paths>({
		baseUrl: API_ORIGIN,
		fetch: fetchImpl,
		credentials: 'same-origin'
	});
}

/** Проверить приглашение перед показом формы регистрации (404 invite_not_found, 410 invite_gone). */
export async function peekInvite(token: string, fetchImpl: typeof fetch = defaultFetch): Promise<InvitePeekOut> {
	const client = createPublicClient(fetchImpl);
	return call(client.GET('/api/v1/invites/{token}', { params: { path: { token } } }));
}

/** Зарегистрироваться по приглашению: учётка user, сессия и коды восстановления (409 login_taken). */
export async function acceptInvite(
	token: string,
	login: string,
	password: string,
	fetchImpl: typeof fetch = defaultFetch
): Promise<InviteAcceptOut> {
	const client = createPublicClient(fetchImpl);
	return call(
		client.POST('/api/v1/invites/{token}/accept', {
			params: { path: { token } },
			body: { login, password }
		})
	);
}

/** Запросить код восстановления пароля (если есть Telegram-аккаунт в сети — уходит в «Избранное»). */
export async function recoverStart(login: string, fetchImpl: typeof fetch = defaultFetch): Promise<void> {
	const client = createPublicClient(fetchImpl);
	await call(client.POST('/api/v1/auth/recover/start', { body: { login } }));
}

/** Завершить восстановление пароля по коду из Telegram или коду восстановления. */
export async function recoverFinish(
	body: RecoverFinishIn,
	fetchImpl: typeof fetch = defaultFetch
): Promise<MeOut> {
	const client = createPublicClient(fetchImpl);
	return call(client.POST('/api/v1/auth/recover/finish', { body }));
}
