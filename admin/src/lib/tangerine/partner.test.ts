import { describe, expect, it } from 'vitest';
import type { TangerinePartnerOut } from '$lib/api/types';
import { partnerLine } from './partner';

const out = (over: Partial<TangerinePartnerOut>): TangerinePartnerOut => ({
	reply_to: 7,
	sender: null,
	account: null,
	status: 'ok',
	...over
});

describe('partnerLine', () => {
	it('свой аккаунт — «обмен с» и ссылка на него', () => {
		const line = partnerLine(
			out({ sender: { tg_user_id: 42, name: 'Анна', username: 'anna' }, account: { id: 3, name: 'twink' } })
		);
		expect(line).toEqual({ text: '🍊 обмен с twink', href: '/a/3', bad: false });
	});

	it('чужой игрок — имя в Telegram и @username', () => {
		expect(partnerLine(out({ sender: { tg_user_id: 42, name: 'Анна К', username: 'anna' } }))).toEqual({
			text: '🍊 дарим: Анна К (@anna)',
			href: null,
			bad: false
		});
		expect(partnerLine(out({ sender: { tg_user_id: 42, name: 'Анна', username: null } }))?.text).toBe('🍊 дарим: Анна');
	});

	it('сообщения нет — дарить некуда', () => {
		expect(partnerLine(out({ status: 'missing' }))).toEqual({
			text: '🍊 сообщение не найдено — дарить некуда',
			href: null,
			bad: true
		});
	});

	it('адресат не задан, Telegram не спросить или ответа ещё нет — ничего', () => {
		expect(partnerLine(out({ reply_to: null, status: 'unset' }))).toBeNull();
		expect(partnerLine(out({ status: 'offline' }))).toBeNull();
		expect(partnerLine(null)).toBeNull();
	});
});
