import type { AccountApi } from '$lib/api/account';
import { call } from '$lib/api/client';
import { ApiFailure } from '$lib/api/errors';
import type { LiveEvent } from '$lib/live/sse';

/** Ручное управление аккаунтом из админки: пока оно идёт, бот не начинает новых решений. Срок —
 * из статуса движка, кадра `manual_control` и ответа на взятие; по сроку кадра нет — управление
 * снимается по часам страницы. */
export class ManualControl {
	until = $state<string | null>(null);
	busy = $state(false);
	error = $state('');
	#api: AccountApi;
	#timer: ReturnType<typeof setTimeout> | null = null;

	constructor(api: AccountApi) {
		this.#api = api;
	}

	/** Новый срок; прошедший — управления нет. */
	sync(until: string | null): void {
		if (this.#timer !== null) clearTimeout(this.#timer);
		this.#timer = null;
		const left = until === null ? 0 : Date.parse(until) - Date.now();
		if (!(left > 0)) {
			this.until = null;
			return;
		}
		this.until = until;
		this.#timer = setTimeout(() => {
			this.#timer = null;
			this.until = null;
		}, left);
	}

	take(): Promise<void> {
		return this.#set(true);
	}

	release(): Promise<void> {
		return this.#set(false);
	}

	async #set(on: boolean): Promise<void> {
		this.busy = true;
		this.error = '';
		try {
			const out = await call(this.#api.POST('/engine/manual-control', { body: { on } }));
			this.sync(out.until);
		} catch (e) {
			this.error = e instanceof ApiFailure ? e.message : String(e);
		} finally {
			this.busy = false;
		}
	}

	onEvent(event: LiveEvent): void {
		if (event.type === 'manual_control') this.sync(event.data.until);
	}

	stop(): void {
		if (this.#timer !== null) clearTimeout(this.#timer);
		this.#timer = null;
	}
}
