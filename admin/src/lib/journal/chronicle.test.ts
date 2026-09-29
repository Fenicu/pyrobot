import { describe, expect, it } from 'vitest';
import type { JournalItem, JournalPage } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import { journalWithRuns } from '$lib/test/journal';
import { chronicle, runCounts, runOutcome, runReply, type RunGroup } from './chronicle';

const page = fixture<JournalPage>('journal_page');
const items = journalWithRuns().items;
const keys = (g: RunGroup) => g.items.map((i) => `${i.type}:${i.id}`);

describe('хроника журнала', () => {
	it('сон: решение, три шага и ответы игры — одна строка на месте самой новой записи', () => {
		const rows = chronicle(items);
		expect(rows.slice(0, 5).map((r) => r.key)).toEqual(['decision:344', 'decision:343', 'run:90', 'decision:341', 'run:89']);
		const sleep = rows[2] as RunGroup;
		expect(keys(sleep)).toEqual([
			'message:778',
			'message:777',
			'action:474',
			'message:776',
			'action:473',
			'message:775',
			'action:472',
			'decision:342'
		]);
		expect(sleep.decision?.scenario).toBe('sleep');
		expect(sleep.started).toBe(sleep.decision?.at);
		expect(runOutcome(sleep)).toEqual({ text: 'выполнено', tone: 'ok' });
		expect(runReply(sleep)).toBe('Ты отправился спать красиво в отель на 7 часов за 213 💵');
		expect(runCounts(sleep)).toBe('3 команды · 4 сообщения');
		expect(runCounts(rows[4] as RunGroup)).toBe('2 команды · 2 сообщения');
	});

	it('ничего не теряется и не повторяется', () => {
		const rows = chronicle(items);
		const all = rows.flatMap((r) => (r.kind === 'run' ? r.items : [r.item])).map((i) => `${i.type}:${i.id}`);
		expect(all.toSorted()).toEqual(items.map((i) => `${i.type}:${i.id}`).toSorted());
		expect(new Set(all).size).toBe(all.length);
	});

	it('сервер без run_id — лента как была, строка на запись', () => {
		const rows = chronicle(page.items);
		expect(rows.every((r) => r.kind === 'item')).toBe(true);
		expect(rows).toHaveLength(page.items.length);
	});

	it('ответы — только из чатов шагов и до итога последнего шага с запасом', () => {
		const at = (s: number) => new Date(Date.UTC(2026, 8, 27, 12, 0, s)).toISOString();
		const step: JournalItem = {
			type: 'action', id: 1, at: at(0), source: 'scenario', kind: 'send', chat_id: 10, command_class: 'nav',
			status: 'confirmed', reason: '', text: '/me', data: null, finished_at: at(1), run_id: 5
		};
		const msg = (id: number, s: number, chat = 10): JournalItem => ({
			type: 'message', id, at: at(s), chat_id: chat, msg_id: id, revision: 0, kind: 'new', date: at(s),
			outgoing: false, recovered: false, text: `m${id}`, markup: null, events: []
		});
		const rows = chronicle([msg(4, 9), msg(3, 2, 20), msg(2, 3), step]);
		expect(rows.map((r) => r.key)).toEqual(['message:4', 'message:3', 'run:5']);
		expect(keys(rows[2] as RunGroup)).toEqual(['message:2', 'action:1']);
	});

	it('итог запуска: отказ шага, идущий, подавленный в dry_run', () => {
		const g = (statuses: string[]): RunGroup =>
			({ actions: statuses.map((status) => ({ status })) }) as unknown as RunGroup;
		expect(runOutcome(g(['confirmed', 'refused']))).toEqual({ text: 'отказ', tone: 'bad' });
		expect(runOutcome(g(['confirmed', 'sent']))).toEqual({ text: 'идёт', tone: 'muted' });
		expect(runOutcome(g(['confirmed', 'suppressed']))).toEqual({ text: 'подавлено', tone: 'warn' });
	});
});
