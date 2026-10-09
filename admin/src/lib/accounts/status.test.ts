import { describe, expect, it } from 'vitest';
import type { AccountOut } from '$lib/api/types';
import { accountActivity, accountDotLabel, accountTone, needsAttention } from './status';

const NOW = new Date('2026-10-09T12:00:00Z');

function acc(patch: Partial<AccountOut> = {}): AccountOut {
	return {
		id: 1,
		name: 'Тест',
		status: 'enabled',
		status_reason: null,
		blocked: false,
		blocked_reason: null,
		killed: false,
		paused: false,
		mode: 'live',
		company: null,
		team_tag: null,
		last_action_at: null,
		level: null,
		busy: null,
		in_metro: false,
		alert: null,
		tg: { online: true, user_id: 1 },
		unread: { error: 0, warn: 0 },
		...patch
	};
}

describe('accountTone', () => {
	const cases: [string, Partial<AccountOut>, string][] = [
		['здоровый', {}, 'ok'],
		['выключен', { status: 'disabled' }, 'off'],
		['удаляется', { status: 'deleting' }, 'off'],
		['выключен важнее ошибки', { status: 'disabled', blocked: true, unread: { error: 2, warn: 0 } }, 'off'],
		['ошибка статуса', { status: 'error' }, 'bad'],
		['заблокирован', { blocked: true }, 'bad'],
		['kill', { killed: true }, 'bad'],
		['непрочитанная ошибка', { unread: { error: 1, warn: 0 } }, 'bad'],
		['пауза и ошибка — ошибка', { paused: true, status: 'error' }, 'bad'],
		['пауза и kill — kill', { paused: true, killed: true }, 'bad'],
		['пауза', { paused: true }, 'warn'],
		['непрочитанное предупреждение', { unread: { error: 0, warn: 3 } }, 'warn'],
		['Telegram не в сети', { tg: { online: false, user_id: null } }, 'warn'],
		['Telegram не в сети и ошибка — ошибка', { tg: { online: false, user_id: null }, killed: true }, 'bad']
	];
	it.each(cases)('%s → %s', (_name, patch, tone) => {
		expect(accountTone(acc(patch))).toBe(tone);
	});
});

describe('accountDotLabel', () => {
	it('удаляемый — «удаляется», остальные — по тону', () => {
		expect(accountDotLabel(acc({ status: 'deleting' }))).toBe('удаляется');
		expect(accountDotLabel(acc({ status: 'disabled' }))).toBe('выключен');
		expect(accountDotLabel(acc({ blocked: true }))).toBe('ошибка');
		expect(accountDotLabel(acc())).toBe('работает');
	});
});

describe('needsAttention', () => {
	it('bad и warn — да, ok и off — нет', () => {
		expect(needsAttention(acc({ blocked: true }))).toBe(true);
		expect(needsAttention(acc({ paused: true }))).toBe(true);
		expect(needsAttention(acc())).toBe(false);
		expect(needsAttention(acc({ status: 'disabled', blocked: true }))).toBe(false);
	});
});

describe('accountActivity', () => {
	const until = '2026-10-09T12:40:00Z';
	const alert = { level: 'error' as const, text: 'Не хватает денег' };

	it('удаляется и выключен', () => {
		expect(accountActivity(acc({ status: 'deleting' }), NOW)).toEqual({
			text: 'удаляется',
			until: null,
			tone: 'muted'
		});
		expect(accountActivity(acc({ status: 'disabled' }), NOW)).toEqual({
			text: 'выключен',
			until: null,
			tone: 'muted'
		});
		expect(accountActivity(acc({ status: 'disabled', blocked: true }), NOW).text).toBe('выключен');
	});

	it('блокировка: причина или подпись по умолчанию', () => {
		expect(accountActivity(acc({ blocked: true, blocked_reason: 'бан' }), NOW)).toEqual({
			text: 'бан',
			until: null,
			tone: 'bad'
		});
		expect(accountActivity(acc({ blocked: true }), NOW).text).toBe('заблокирован');
	});

	it('ошибка статуса: причина или «ошибка»; блокировка важнее', () => {
		expect(accountActivity(acc({ status: 'error', status_reason: 'нет сессии' }), NOW)).toEqual({
			text: 'нет сессии',
			until: null,
			tone: 'bad'
		});
		expect(accountActivity(acc({ status: 'error' }), NOW).text).toBe('ошибка');
		expect(accountActivity(acc({ status: 'error', blocked: true, blocked_reason: 'бан' }), NOW).text).toBe('бан');
	});

	it('предупреждение: в карточке — текст, в колонке — состояние', () => {
		const a = acc({ alert });
		expect(accountActivity(a, NOW, { card: true })).toEqual({
			text: 'Не хватает денег',
			until: null,
			tone: 'bad'
		});
		expect(accountActivity(acc({ alert: { level: 'warn', text: 'Мало сил' } }), NOW, { card: true }).tone).toBe('warn');
		expect(accountActivity(a, NOW).text).toBe('свободен');
		expect(accountActivity(a, NOW, { card: false }).text).toBe('свободен');
		// Блокировка важнее предупреждения даже в карточке.
		expect(accountActivity(acc({ alert, blocked: true }), NOW, { card: true }).text).toBe('заблокирован');
	});

	it('kill, dry_run, пауза, метро', () => {
		expect(accountActivity(acc({ killed: true }), NOW)).toEqual({
			text: 'kill',
			until: null,
			tone: 'bad'
		});
		expect(accountActivity(acc({ mode: 'dry_run' }), NOW)).toEqual({
			text: 'dry_run',
			until: null,
			tone: 'muted'
		});
		expect(accountActivity(acc({ paused: true }), NOW)).toEqual({
			text: 'пауза',
			until: null,
			tone: 'warn'
		});
		expect(accountActivity(acc({ in_metro: true }), NOW)).toEqual({
			text: 'метро',
			until: null,
			tone: 'muted'
		});
	});

	it('порядок: kill → dry_run → пауза → метро → занятость', () => {
		const busy = { activity: 'learn', until };
		expect(accountActivity(acc({ killed: true, mode: 'dry_run', paused: true }), NOW).text).toBe('kill');
		expect(accountActivity(acc({ mode: 'dry_run', paused: true, in_metro: true }), NOW).text).toBe('dry_run');
		expect(accountActivity(acc({ paused: true, in_metro: true, busy }), NOW).text).toBe('пауза');
		expect(accountActivity(acc({ in_metro: true, busy }), NOW).text).toBe('метро');
	});

	it('занятость до конца — с подписью и временем', () => {
		expect(accountActivity(acc({ busy: { activity: 'learn', until } }), NOW)).toEqual({
			text: 'учёба',
			until,
			tone: 'muted'
		});
	});

	it('занятость закончилась между опросами — «свободен»', () => {
		const past = new Date(NOW.getTime() - 60_000).toISOString();
		expect(accountActivity(acc({ busy: { activity: 'learn', until: past } }), NOW)).toEqual({
			text: 'свободен',
			until: null,
			tone: 'muted'
		});
		expect(accountActivity(acc({ busy: { activity: 'learn', until: NOW.toISOString() } }), NOW).text).toBe('свободен');
		expect(accountActivity(acc({ busy: { activity: 'learn', until: 'не дата' } }), NOW).text).toBe('свободен');
	});

	it('без занятости — «свободен»', () => {
		expect(accountActivity(acc(), NOW)).toEqual({
			text: 'свободен',
			until: null,
			tone: 'muted'
		});
	});
});
