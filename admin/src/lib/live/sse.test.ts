import { describe, expect, it } from 'vitest';
import { fixtureText } from '$lib/test/fixtures';
import { decodeEvent, isActionCreated, splitFrames, type LiveEvent } from './sse';

function decodeAll(file: string): LiveEvent[] {
	return splitFrames(fixtureText(file)).map((f) => {
		const event = decodeEvent(f.event, f.data, f.id);
		expect(event, `${f.id} ${f.event}`).not.toBeNull();
		return event as LiveEvent;
	});
}

describe('кадры SSE с прода', () => {
	it('все кадры разбираются в типы', () => {
		const events = decodeAll('sse_frames_all.txt');
		const types = new Set(events.map((e) => e.type));
		expect(types).toEqual(new Set(['decision', 'settings', 'action', 'message', 'state', 'scenario_run']));
		expect(events[0]).toMatchObject({
			type: 'decision',
			id: '1790538450815:1',
			data: { id: 344, kind: 'wait', reason: 'busy' }
		});
	});

	it('действие: создание и обновление различаются', () => {
		const actions = decodeAll('sse_frames_all.txt').filter((e) => e.type === 'action');
		const [created, sent] = actions;
		expect(created?.type === 'action' && isActionCreated(created.data)).toBe(true);
		expect(created?.data).toMatchObject({ id: 475, status: 'intent', source: 'manual', text: '/inv' });
		expect(sent?.type === 'action' && isActionCreated(sent.data)).toBe(false);
		expect(sent?.data).toEqual({ id: 475, status: 'sent', reason: '' });
	});

	it('сообщение без кнопок у старого сервера — markup null; state — changed целиком', () => {
		const events = decodeAll('sse_frames_all.txt');
		const message = events.find((e) => e.type === 'message');
		expect(message?.type).toBe('message');
		if (message?.type !== 'message') return;
		expect(message.data).toMatchObject({ journal_id: 779, msg_id: 3626317, revision: 0, markup: null });
		expect(message.data.text).toContain('Гаджеты при тебе');
		const state = events.find((e) => e.type === 'state');
		if (state?.type !== 'state') throw new Error('no state frame');
		expect(state.data.version).toBe(639);
		expect(state.data.changed.busy).toEqual({
			value: { activity: 'sleep_hotel', until: '2026-09-28T02:05:09Z' },
			at: '2026-09-27T19:05:09Z',
			src: 'screen'
		});
	});

	it('reset', () => {
		expect(decodeAll('sse_reset.txt')).toEqual([
			{ type: 'reset', id: '1790538450815:2', data: { reason: 'new' } }
		]);
	});

	it('сообщение с кнопками (после 0.4) и уведомление (форма из app/main.py)', () => {
		const markup = { inline: [['В отеле - 213 💵', 1, 0, 'sleep_Hotel', null, null]] };
		const msg = decodeEvent(
			'message',
			JSON.stringify({
				journal_id: 1,
				chat_id: 227859379,
				msg_id: 3626304,
				revision: 1790535904,
				kind: 'edit',
				date: '2026-09-27T19:05:04+00:00',
				outgoing: false,
				text: 'Где собираешься спать?',
				events: [],
				markup
			})
		);
		expect(msg?.type === 'message' && msg.data.markup).toEqual(markup);
		const note = decodeEvent(
			'notification',
			'{"id": 10, "level": "warn", "code": "tg_auth_lost", "text": "telegram session revoked"}'
		);
		expect(note).toMatchObject({ type: 'notification', data: { id: 10, level: 'warn' } });
	});

	it('незнакомый тип, битый JSON и чужая форма — null', () => {
		expect(decodeEvent('ping', '{}')).toBeNull();
		expect(decodeEvent('state', '{oops')).toBeNull();
		expect(decodeEvent('state', '{"changed": {}}')).toBeNull();
		expect(decodeEvent('reset', '{"reason": "later"}')).toBeNull();
		expect(decodeEvent('message', '[]')).toBeNull();
	});

	it('комментарии-пинги пропускаются', () => {
		expect(splitFrames(': ping\n\nid: e:1\nevent: reset\ndata: {"reason": "new"}\n\n')).toEqual([
			{ id: 'e:1', event: 'reset', data: '{"reason": "new"}' }
		]);
	});
});
