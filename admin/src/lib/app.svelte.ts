import { goto } from '$app/navigation';
import { createApi, EVENTS_URL } from '$lib/api/client';
import { LiveConnection } from '$lib/live/connection.svelte';
import { CharacterStore } from '$lib/stores/character.svelte';
import { EngineStore } from '$lib/stores/engine.svelte';
import { Session } from '$lib/stores/session.svelte';
import { UnreadCounter } from '$lib/stores/unread.svelte';

/** Синглтоны вкладки: сессия, клиент API, поток событий и общие счётчики. */
export const session = new Session(undefined, () => void goto('/login'));
export const api = createApi(session.hooks);
export const live = new LiveConnection({
	url: EVENTS_URL,
	create: (url) => new EventSource(url),
	checkSession: () => session.load(),
	onUnauthorized: () => session.expire()
});
export const unread = new UnreadCounter(api);
export const engine = new EngineStore(api);
export const character = new CharacterStore(api);

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
