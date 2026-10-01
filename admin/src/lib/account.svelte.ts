import { createAccountApi, eventsUrl, type AccountApi } from '$lib/api/account';
import type { SessionHooks } from '$lib/api/client';
import { LiveConnection, type EventSourceLike, type SessionCheck } from '$lib/live/connection.svelte';
import { CharacterStore } from '$lib/stores/character.svelte';
import { EngineStore } from '$lib/stores/engine.svelte';
import { UnreadCounter } from '$lib/stores/unread.svelte';

export interface AccountDeps {
	hooks: SessionHooks;
	createSource: (url: string) => EventSourceLike;
	checkSession: () => Promise<SessionCheck>;
	onUnauthorized: () => void;
	fetch?: typeof fetch;
}

/** Всё, что живёт, пока открыт аккаунт: клиент API, поток событий, статус движка, персонаж и
 * непрочитанные. Экземпляры — свои у каждого контекста: ответ, пришедший после `stop()`, пишет в
 * брошенный контекст, а не в контекст другого аккаунта. */
export class AccountContext {
	readonly id: number;
	readonly api: AccountApi;
	readonly live: LiveConnection;
	readonly unread: UnreadCounter;
	readonly engine: EngineStore;
	readonly character: CharacterStore;
	#unsubscribe: (() => void) | null = null;

	constructor(id: number, deps: AccountDeps) {
		this.id = id;
		this.api = createAccountApi(deps.hooks, id, deps.fetch);
		this.live = new LiveConnection({
			url: eventsUrl(id),
			create: deps.createSource,
			checkSession: deps.checkSession,
			onUnauthorized: deps.onUnauthorized
		});
		this.unread = new UnreadCounter(this.api);
		this.engine = new EngineStore(this.api);
		this.character = new CharacterStore(this.api);
	}

	/** Поток событий и счётчики; на `reset` каждый перечитывает своё. */
	start(): void {
		if (this.#unsubscribe !== null) return;
		this.#unsubscribe = this.live.subscribe((event) => {
			this.unread.onEvent(event);
			this.engine.onEvent(event);
			this.character.onEvent(event);
		});
		this.live.start();
		void this.unread.load();
		this.engine.start();
		this.character.start();
	}

	/** Закрыть поток и опросы. */
	stop(): void {
		this.#unsubscribe?.();
		this.#unsubscribe = null;
		this.live.stop();
		this.engine.stop();
		this.character.stop();
	}
}

/** Открытый аккаунт вкладки: контекст один, переключение останавливает прежний и создаёт новый. */
export class CurrentAccount {
	ctx = $state<AccountContext | null>(null);
	#create: (id: number) => AccountContext;

	constructor(create: (id: number) => AccountContext) {
		this.#create = create;
	}

	start(id: number): AccountContext {
		this.ctx?.stop();
		const ctx = this.#create(id);
		this.ctx = ctx;
		ctx.start();
		return ctx;
	}

	stop(): void {
		this.ctx?.stop();
		this.ctx = null;
	}

	/** Контекст экрана аккаунта: экраны `/a/[account]` рисуются только при открытом контексте. */
	get(): AccountContext {
		if (this.ctx === null) throw new Error('аккаунт не открыт');
		return this.ctx;
	}
}
