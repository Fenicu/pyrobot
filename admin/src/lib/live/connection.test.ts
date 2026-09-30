import { describe, expect, it, vi } from 'vitest';
import { LiveConnection, MAX_DELAY_MS, type EventSourceLike, type SessionCheck } from './connection.svelte';
import type { LiveEvent } from './sse';

class FakeSource implements EventSourceLike {
	readyState = 0;
	onopen: ((ev: Event) => unknown) | null = null;
	onerror: ((ev: Event) => unknown) | null = null;
	closed = false;
	listeners = new Map<string, (ev: MessageEvent) => void>();
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

function rig(check: SessionCheck = 'ok') {
	const sources: FakeSource[] = [];
	const timers: { fn: () => void; ms: number }[] = [];
	const onUnauthorized = vi.fn();
	const checkSession = vi.fn(async () => check);
	const live = new LiveConnection({
		url: '/api/v1/accounts/1/events',
		create: () => {
			const s = new FakeSource();
			sources.push(s);
			return s;
		},
		checkSession,
		onUnauthorized,
		setTimer: (fn, ms) => {
			timers.push({ fn, ms });
			return timers.length;
		},
		clearTimer: () => {}
	});
	const events: LiveEvent[] = [];
	live.subscribe((e) => events.push(e));
	return { live, sources, timers, onUnauthorized, checkSession, events };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('поток событий', () => {
	it('кадры доходят типизированными, reset — тоже', () => {
		const r = rig();
		r.live.start();
		const s = r.sources[0]!;
		s.open();
		expect(r.live.status).toBe('open');
		s.send('reset', '{"reason": "new"}', 'e:1');
		s.send('action', '{"id": 5, "status": "sent", "reason": ""}', 'e:2');
		s.send('action', 'not json', 'e:3');
		expect(r.events).toEqual([
			{ type: 'reset', id: 'e:1', data: { reason: 'new' } },
			{ type: 'action', id: 'e:2', data: { id: 5, status: 'sent', reason: '' } }
		]);
	});

	it('обрыв сети — браузер переподключается сам', async () => {
		const r = rig();
		r.live.start();
		r.sources[0]!.open();
		r.sources[0]!.fail(0);
		await flush();
		expect(r.live.status).toBe('reconnecting');
		expect(r.checkSession).not.toHaveBeenCalled();
		expect(r.sources).toHaveLength(1);
		r.sources[0]!.open();
		expect(r.live.status).toBe('open');
	});

	it('закрытый поток и 401 — выход на вход', async () => {
		const r = rig('unauthorized');
		r.live.start();
		r.sources[0]!.fail(2);
		await flush();
		expect(r.checkSession).toHaveBeenCalledTimes(1);
		expect(r.onUnauthorized).toHaveBeenCalledTimes(1);
		expect(r.timers).toEqual([]);
	});

	it('закрытый поток без 401 — «нет связи» и новое соединение с растущей паузой', async () => {
		const r = rig('error');
		r.live.start();
		for (let i = 0; i < 7; i++) {
			r.sources[i]!.fail(2);
			await flush();
			expect(r.live.status).toBe('offline');
			r.timers[i]!.fn();
		}
		expect(r.timers.map((t) => t.ms)).toEqual([1000, 2000, 4000, 8000, 16000, MAX_DELAY_MS, MAX_DELAY_MS]);
		expect(r.sources).toHaveLength(8);
		expect(r.sources.slice(0, 7).every((s) => s.closed)).toBe(true);
		// Удачное подключение сбрасывает паузу.
		r.sources[7]!.open();
		r.sources[7]!.fail(2);
		await flush();
		expect(r.timers.at(-1)?.ms).toBe(1000);
	});

	it('stop закрывает поток и не переподключает', async () => {
		const r = rig('error');
		r.live.start();
		r.sources[0]!.fail(2);
		r.live.stop();
		await flush();
		expect(r.timers).toEqual([]);
		expect(r.live.status).toBe('idle');
	});
});
