import createClient from 'openapi-fetch';
import { API_ORIGIN, type SessionHooks } from '$lib/api/client';
import { normalizeError, type ApiError } from '$lib/api/errors';
import type { paths } from '$lib/api/schema';

export type SessionStatus = 'unknown' | 'authenticated' | 'anonymous';

/** Сессия админа: cookie httpOnly ставит сервер, CSRF-токен живёт только в памяти вкладки и
 * после перезагрузки берётся из `GET /auth/me`. */
export class Session {
	login = $state<string | null>(null);
	status = $state<SessionStatus>('unknown');
	#csrf: string | null = null;
	#client;
	#onExpire: () => void;

	constructor(fetchImpl: typeof fetch = (r) => fetch(r), onExpire: () => void = () => {}) {
		this.#client = createClient<paths>({
			baseUrl: API_ORIGIN,
			fetch: fetchImpl,
			credentials: 'same-origin'
		});
		this.#onExpire = onExpire;
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
				this.#set(data.login, data.csrf_token);
				return 'ok';
			}
			if (response.status === 401) {
				this.clear();
				return 'unauthorized';
			}
		} catch {
			// сеть — ниже
		}
		if (this.status === 'unknown') this.status = 'anonymous';
		return 'error';
	}

	async signIn(login: string, password: string): Promise<ApiError | null> {
		try {
			const { data, error, response } = await this.#client.POST('/api/v1/auth/login', {
				body: { login, password }
			});
			if (data) {
				this.#set(data.login, data.csrf_token);
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

	/** 401 от любого запроса: токен забыт, дальше — вход. */
	expire(): void {
		const was = this.status;
		this.clear();
		if (was !== 'anonymous') this.#onExpire();
	}

	clear(): void {
		this.#csrf = null;
		this.login = null;
		this.status = 'anonymous';
	}

	#set(login: string, csrf: string): void {
		this.login = login;
		this.#csrf = csrf;
		this.status = 'authenticated';
	}
}
