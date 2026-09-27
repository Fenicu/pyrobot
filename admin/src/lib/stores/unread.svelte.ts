import { call, type Api } from '$lib/api/client';
import type { LiveEvent } from '$lib/live/sse';

/** Счётчик непрочитанных уведомлений для иконки в меню. */
export class UnreadCounter {
	count = $state(0);
	#api: Api;

	constructor(api: Api) {
		this.#api = api;
	}

	async load(): Promise<void> {
		try {
			const page = await call(
				this.#api.GET('/api/v1/notifications', { params: { query: { unread: true, limit: 1 } } })
			);
			this.count = page.unread;
		} catch {
			// счётчик — не критичен, перечитается на reset
		}
	}

	onEvent(event: LiveEvent): void {
		if (event.type === 'notification') this.count += 1;
		else if (event.type === 'reset') void this.load();
	}
}
