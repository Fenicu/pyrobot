import { goto } from '$app/navigation';
import { AccountContext, CurrentAccount } from '$lib/account.svelte';
import { createApi } from '$lib/api/client';
import { loginHref } from '$lib/nav';
import { AccountsStore } from '$lib/stores/accounts.svelte';
import { Session } from '$lib/stores/session.svelte';

/** Синглтоны вкладки: сессия, глобальный клиент API, список аккаунтов и открытый аккаунт. */
export const session = new Session(undefined, () => void goto(loginHref(new URL(location.href))));
export const api = createApi(session.hooks);
export const accounts = new AccountsStore(api);
export const current = new CurrentAccount(
	(id) =>
		new AccountContext(id, {
			hooks: session.hooks,
			createSource: (url) => new EventSource(url),
			checkSession: () => session.load(),
			onUnauthorized: () => session.expire()
		})
);

/** Открыть аккаунт: прежний контекст останавливается, новый — со своими потоком и хранилищами. */
export function startAccount(id: number): AccountContext {
	return current.start(id);
}

/** После входа — общее (список аккаунтов); аккаунт открывает макет `/a/[account]`. */
export function startApp(): void {
	accounts.start();
}

export function stopApp(): void {
	current.stop();
	accounts.stop();
}
