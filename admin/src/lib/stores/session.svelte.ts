import createClient from 'openapi-fetch';
import { API_ORIGIN, type SessionHooks } from '$lib/api/client';
import { normalizeError, type ApiError } from '$lib/api/errors';
import type { paths } from '$lib/api/schema';
import type { MeOut } from '$lib/api/types';
import { FIRST_DELAY_MS, MAX_DELAY_MS } from '$lib/live/connection.svelte';

export type SessionStatus = 'unknown' | 'authenticated' | 'anonymous';

export interface Timers {
	setTimer: (fn: () => void, ms: number) => unknown;
	clearTimer: (handle: unknown) => void;
}

const REAL_TIMERS: Timers = {
	setTimer: (fn, ms) => setTimeout(fn, ms),
	clearTimer: (h) => clearTimeout(h as ReturnType<typeof setTimeout>)
};

/** Сессия админа: cookie httpOnly ставит сервер, CSRF-токен живёт только в памяти вкладки и
 * после перезагрузки берётся из `GET /auth/me`. */
export class Session {
	login = $state<string | null>(null);
	role = $state<'owner' | 'user' | null>(null);
	status = $state<SessionStatus>('unknown');
	/** Старт: `/auth/me` не ответил (сеть, 5xx) — сессия неизвестна, идёт повтор. */
	offline = $state(false);
	/** Пауза до следующей попытки старта, мс. */
	retryIn = $state(0);
	#csrf: string | null = null;
	#client;
	#onExpire: () => void;
	#timers: Timers;
	#timer: unknown = null;
	#delay = FIRST_DELAY_MS;
	#starting: Promise<void> | null = null;

	constructor(
		fetchImpl: typeof fetch = (r) => fetch(r),
		onExpire: () => void = () => {},
		timers: Timers = REAL_TIMERS
	) {
		this.#client = createClient<paths>({
			baseUrl: API_ORIGIN,
			fetch: fetchImpl,
			credentials: 'same-origin'
		});
		this.#onExpire = onExpire;
		this.#timers = timers;
	}

	get csrf(): string | null {
		return this.#csrf;
	}

	readonly hooks: SessionHooks = {
		csrf: () => this.#csrf,
		refreshCsrf: async () => ((await this.load()) === 'ok' ? this.#csrf : null),
		unauthorized: () => this.expire()
	};

	/** `GET /auth/me`: `ok` — сессия есть, `unauthorized` — нет, `error` — сервер недоступен. */
	async load(): Promise<'ok' | 'unauthorized' | 'error'> {
		try {
			const { data, response } = await this.#client.GET('/api/v1/auth/me');
			if (data) {
				this.#set(data.login, data.csrf_token, data.role ?? null);
				return 'ok';
			}
			if (response.status === 401) {
				this.clear();
				return 'unauthorized';
			}
		} catch {
			// сеть — ниже
		}
		// Сеть или 5xx (502 во время выката) — не «вышел»: статус прежний.
		return 'error';
	}

	/** Старт вкладки: `/auth/me`, пока сессия неизвестна; сбой — повтор с растущей паузой
	 * 1 → 30 с, как у потока событий. */
	start(): Promise<void> {
		this.#starting ??= this.#attempt().finally(() => (this.#starting = null));
		return this.#starting;
	}

	/** «Повторить» — сразу, не дожидаясь паузы. */
	retry(): void {
		this.#cancelRetry();
		void this.start();
	}

	stop(): void {
		this.#cancelRetry();
		this.#settled();
	}

	async #attempt(): Promise<void> {
		this.#cancelRetry();
		if (this.status !== 'unknown') return this.#settled();
		await this.load();
		if (this.status !== 'unknown') return this.#settled();
		const wait = this.#delay;
		this.#delay = Math.min(this.#delay * 2, MAX_DELAY_MS);
		this.offline = true;
		this.retryIn = wait;
		this.#timer = this.#timers.setTimer(() => {
			this.#timer = null;
			void this.start();
		}, wait);
	}

	#cancelRetry(): void {
		if (this.#timer !== null) this.#timers.clearTimer(this.#timer);
		this.#timer = null;
	}

	#settled(): void {
		this.offline = false;
		this.retryIn = 0;
		this.#delay = FIRST_DELAY_MS;
	}

	async signIn(login: string, password: string): Promise<ApiError | null> {
		try {
			const { data, error, response } = await this.#client.POST('/api/v1/auth/login', {
				body: { login, password }
			});
			if (data) {
				this.#set(data.login, data.csrf_token, data.role ?? null);
				return null;
			}
			return normalizeError(response.status, error, response.headers);
		} catch (e) {
			return { kind: 'network', status: 0, message: String(e) };
		}
	}

	/** Выход: сессия считается закрытой только при 204 (и 401 — её уже нет). Сбой сети или 403
	 * — ошибка, сессия и токен остаются, выход можно повторить (при 403 токен перечитан). */
	async signOut(): Promise<ApiError | null> {
		const token = this.#csrf;
		let response: Response;
		let body: unknown;
		try {
			({ error: body, response } = await this.#client.POST('/api/v1/auth/logout', {
				headers: token ? { 'X-CSRF-Token': token } : {}
			}));
		} catch (e) {
			return { kind: 'network', status: 0, message: String(e) };
		}
		if (response.status === 204 || response.status === 401) {
			this.clear();
			return null;
		}
		const error = normalizeError(response.status, body, response.headers);
		if (error.kind === 'csrf' && (await this.load()) === 'unauthorized') return null;
		return error;
	}

	/** Принять данные сессии (после регистрации по приглашению или восстановления пароля). */
	adopt(me: MeOut): void {
		this.#set(me.login, me.csrf_token, me.role);
	}

	/** 401 от любого запроса: токен забыт, дальше — вход. */
	expire(): void {
		const was = this.status;
		this.clear();
		if (was !== 'anonymous') this.#onExpire();
	}

	clear(): void {
		this.#csrf = null;
		this.login = null;
		this.role = null;
		this.status = 'anonymous';
		this.stop();
	}

	#set(login: string, csrf: string, role: 'owner' | 'user' | null = null): void {
		this.login = login;
		this.#csrf = csrf;
		this.role = role;
		this.status = 'authenticated';
		this.stop();
	}
}
