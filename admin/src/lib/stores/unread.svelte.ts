import type { AccountApi } from '$lib/api/account';
import { call } from '$lib/api/client';
import type { LiveEvent } from '$lib/live/sse';

/** Счётчик для значка в меню: непрочитанные warn и error. Info — эхо своих действий (пауза, режим) и
 * справки — на значок не влияют, они видны в «непрочитанных» на экране уведомлений. */
export class UnreadCounter {
	count = $state(0);
	#api: AccountApi;

	constructor(api: AccountApi) {
		this.#api = api;
	}

	async load(): Promise<void> {
		try {
			const page = await call(
				this.#api.GET('/notifications', { params: { query: { unread: true, limit: 1 } } })
			);
			this.count = page.unread_alerts;
		} catch {
			// счётчик — не критичен, перечитается на reset
		}
	}

	onEvent(event: LiveEvent): void {
		if (event.type === 'notification' && event.data.level !== 'info') this.count += 1;
		else if (event.type === 'reset') void this.load();
	}
}
