import { decodeEvent, LIVE_TYPES, type LiveEvent } from './sse';

/** Минимум EventSource, который нужен соединению (в тестах — подделка). */
export interface EventSourceLike {
	readonly readyState: number;
	onopen: ((ev: Event) => unknown) | null;
	onerror: ((ev: Event) => unknown) | null;
	addEventListener(type: string, listener: (ev: MessageEvent) => void): void;
	close(): void;
}

export type SessionCheck = 'ok' | 'unauthorized' | 'error';
export type LiveStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'offline';

export interface LiveDeps {
	url: string;
	create: (url: string) => EventSourceLike;
	/** GET /auth/me после закрытого потока: 401 → выход, иначе — переподключение с паузой. */
	checkSession: () => Promise<SessionCheck>;
	onUnauthorized: () => void;
	setTimer?: (fn: () => void, ms: number) => unknown;
	clearTimer?: (handle: unknown) => void;
}

const CLOSED = 2;
export const FIRST_DELAY_MS = 1000;
export const MAX_DELAY_MS = 30_000;

/** Один EventSource на вкладку. Сетевые обрывы браузер переподключает сам (с Last-Event-ID);
 * закрытый поток (не-200, сессия) — проверка сессии и новое соединение с растущей паузой. */
export class LiveConnection {
	status = $state<LiveStatus>('idle');
	/** Пауза до следующей попытки после закрытого потока, мс. */
	retryIn = $state(0);
	private source: EventSourceLike | null = null;
	private delay = FIRST_DELAY_MS;
	private timer: unknown = null;
	private running = false;
	private handlers = new Set<(event: LiveEvent) => void>();
	private readonly deps: Required<LiveDeps>;

	constructor(deps: LiveDeps) {
		this.deps = {
			setTimer: (fn, ms) => setTimeout(fn, ms),
			clearTimer: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
			...deps
		};
	}

	subscribe(handler: (event: LiveEvent) => void): () => void {
		this.handlers.add(handler);
		return () => this.handlers.delete(handler);
	}

	start(): void {
		if (this.running) return;
		this.running = true;
		this.connect();
	}

	stop(): void {
		this.running = false;
		if (this.timer !== null) this.deps.clearTimer(this.timer);
		this.timer = null;
		this.source?.close();
		this.source = null;
		this.delay = FIRST_DELAY_MS;
		this.retryIn = 0;
		this.status = 'idle';
	}

	private connect(): void {
		this.timer = null;
		this.retryIn = 0;
		this.status = 'connecting';
		const source = this.deps.create(this.deps.url);
		this.source = source;
		source.onopen = () => {
			if (this.source !== source) return;
			this.status = 'open';
			this.delay = FIRST_DELAY_MS;
		};
		source.onerror = () => {
			if (this.source === source) void this.failed(source);
		};
		for (const type of LIVE_TYPES) {
			source.addEventListener(type, (ev) => {
				if (this.source !== source) return;
				const event = decodeEvent(type, String(ev.data), ev.lastEventId);
				if (event !== null) this.emit(event);
			});
		}
	}

	private emit(event: LiveEvent): void {
		for (const handler of this.handlers) {
			try {
				handler(event);
			} catch (e) {
				console.error('live handler failed', e);
			}
		}
	}

	private async failed(source: EventSourceLike): Promise<void> {
		if (source.readyState !== CLOSED) {
			// Обрыв сети: браузер переподключается сам.
			this.status = 'reconnecting';
			return;
		}
		source.close();
		this.source = null;
		this.status = 'offline';
		const check = await this.deps.checkSession();
		if (!this.running || this.source !== null || this.timer !== null) return;
		if (check === 'unauthorized') {
			this.deps.onUnauthorized();
			return;
		}
		const wait = this.delay;
		this.delay = Math.min(this.delay * 2, MAX_DELAY_MS);
		this.retryIn = wait;
		this.timer = this.deps.setTimer(() => this.connect(), wait);
	}
}
