import { goto } from '$app/navigation';
import { createAccountApi, eventsUrl } from '$lib/api/account';
import { createApi } from '$lib/api/client';
import { LiveConnection } from '$lib/live/connection.svelte';
import { loginHref } from '$lib/nav';
import { CharacterStore } from '$lib/stores/character.svelte';
import { EngineStore } from '$lib/stores/engine.svelte';
import { Session } from '$lib/stores/session.svelte';
import { UnreadCounter } from '$lib/stores/unread.svelte';

/** Аккаунт админки, пока нет переключателя аккаунтов. */
const ACCOUNT_ID = 1;

/** Синглтоны вкладки: сессия, клиенты API (глобальный и аккаунта), поток событий аккаунта и
 * общие счётчики. */
export const session = new Session(undefined, () => void goto(loginHref(new URL(location.href))));
export const api = createApi(session.hooks);
export const accountApi = createAccountApi(session.hooks, ACCOUNT_ID);
export const live = new LiveConnection({
	url: eventsUrl(ACCOUNT_ID),
	create: (url) => new EventSource(url),
	checkSession: () => session.load(),
	onUnauthorized: () => session.expire()
});
export const unread = new UnreadCounter(accountApi);
export const engine = new EngineStore(accountApi);
export const character = new CharacterStore(accountApi);

let unsubscribe: (() => void) | null = null;

/** После входа: поток событий и счётчики; на `reset` каждый перечитывает своё. */
export function startApp(): void {
	if (unsubscribe !== null) return;
	unsubscribe = live.subscribe((event) => {
		unread.onEvent(event);
		engine.onEvent(event);
		character.onEvent(event);
	});
	live.start();
	void unread.load();
	engine.start();
	character.start();
}

export function stopApp(): void {
	unsubscribe?.();
	unsubscribe = null;
	live.stop();
	engine.stop();
	character.stop();
}
