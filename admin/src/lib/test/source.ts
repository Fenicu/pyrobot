import type { EventSourceLike } from '$lib/live/connection.svelte';

/** Подделка EventSource: тест сам открывает поток, шлёт кадры и обрывает его. */
export class FakeSource implements EventSourceLike {
	readyState = 0;
	onopen: ((ev: Event) => unknown) | null = null;
	onerror: ((ev: Event) => unknown) | null = null;
	closed = false;
	listeners = new Map<string, (ev: MessageEvent) => void>();
	constructor(readonly url = '') {}
	addEventListener(type: string, listener: (ev: MessageEvent) => void) {
		this.listeners.set(type, listener);
	}
	close() {
		this.closed = true;
		this.readyState = 2;
	}
	open() {
		this.readyState = 1;
		this.onopen?.(new Event('open'));
	}
	send(type: string, data: string, id = '') {
		this.listeners.get(type)?.(new MessageEvent(type, { data, lastEventId: id }));
	}
	fail(readyState: number) {
		this.readyState = readyState;
		this.onerror?.(new Event('error'));
	}
}
