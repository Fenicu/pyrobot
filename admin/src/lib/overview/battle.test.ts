import { describe, expect, it } from 'vitest';
import type { ApiError } from '$lib/api/errors';
import type { BattleHq, HqBattle, HqCompany } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import {
	allowedTargets,
	battleTitle,
	battleWarnings,
	draftKey,
	noteText,
	ordersBody,
	saveErrorText,
	summaryRows,
	toDraft,
	warningLines
} from './battle';

const hq = fixture<BattleHq>('overview_battle');
const first = hq.battles[0]!;
const second = hq.battles[1]!;

describe('допустимые цели строки корпорации', () => {
	it('своя компания убрана, защита и остальные остаются', () => {
		const names = allowedTargets(hq.targets, 'hooli').map((t) => t.name);
		expect(names).not.toContain('🤖Hooli');
		expect(names).toContain('🛡Защита');
		expect(names).toHaveLength(hq.targets.length - 1);
	});

	it('для «всем» (компании нет) — все цели; защита не вылетает ни у кого', () => {
		expect(allowedTargets(hq.targets, null)).toEqual(hq.targets);
		for (const code of hq.companies.map((c) => c.code)) {
			expect(allowedTargets(hq.targets, code).some((t) => t.code === null)).toBe(true);
		}
	});
});

describe('предупреждения над формой', () => {
	it('считают уходящих в защиту и не поставивших цель с причинами', () => {
		expect(battleWarnings(first)).toEqual({
			tooLate: false,
			defense: 1,
			unmanaged: [{ reason: 'paused', count: 1 }]
		});
		expect(battleWarnings(second)).toEqual({ tooLate: false, defense: 0, unmanaged: [{ reason: 'paused', count: 1 }] });
	});

	it('за минуту до битвы: tooLate, а «too_late» не дублируется причиной', () => {
		const late: HqBattle = {
			...first,
			editable: false,
			note: 'too_late',
			accounts: first.accounts.map((a) => ({ ...a, applies: false, note: 'too_late' }))
		};
		expect(battleWarnings(late)).toEqual({ tooLate: true, defense: 0, unmanaged: [] });
	});

	it('причины сгруппированы, порядок — по первому появлению', () => {
		const acc = first.accounts[0]!;
		const many: HqBattle = {
			...first,
			accounts: [
				{ ...acc, id: 11, applies: false, note: 'dry_run' },
				{ ...acc, id: 12, applies: false, note: 'paused' },
				{ ...acc, id: 13, applies: false, note: 'dry_run' }
			]
		};
		expect(battleWarnings(many).unmanaged).toEqual([
			{ reason: 'dry_run', count: 2 },
			{ reason: 'paused', count: 1 }
		]);
	});

	it('тексты строк', () => {
		expect(warningLines(first)).toEqual([
			'1 аккаунт пойдёт в 🛡Защиту: цель — его компания',
			'1 аккаунт цель не поставит: на паузе'
		]);
		const late: HqBattle = { ...first, editable: false, note: 'too_late', accounts: [] };
		expect(warningLines(late)).toEqual(['до битвы меньше минуты: приказ уже не применится']);
		const acc = first.accounts[0]!;
		const crowd: HqBattle = {
			...first,
			accounts: Array.from({ length: 5 }, (_, i) => ({ ...acc, id: 20 + i, applies: false, note: 'manual_control' }))
		};
		expect(warningLines(crowd)).toEqual(['5 аккаунтов цель не поставят: ручное управление']);
		const two: HqBattle = {
			...first,
			accounts: [
				{ ...acc, id: 31, note: 'own_company' },
				{ ...acc, id: 32, note: 'own_company' }
			]
		};
		expect(warningLines(two)).toEqual(['2 аккаунта пойдут в 🛡Защиту: цель — их компания']);
	});

	it('коды причин переводятся, неизвестный код остаётся как есть', () => {
		expect(noteText('paused')).toBe('на паузе');
		expect(noteText('engine_elsewhere')).toBe('движок на другом хосте');
		expect(noteText('dry_run')).toBe('режим dry_run');
		expect(noteText('something_new')).toBe('something_new');
	});
});

describe('тело приказа', () => {
	it('пустые и null-цели отбрасываются', () => {
		expect(ordersBody(null, { bmesa: null, piper: '', hooli: '🤖Hooli' })).toEqual({
			all: null,
			corporations: { hooli: '🤖Hooli' }
		});
		expect(ordersBody('', {})).toEqual({ all: null, corporations: {} });
		expect(ordersBody('☂️Umbrella', { bmesa: '🛡Защита' })).toEqual({
			all: '☂️Umbrella',
			corporations: { bmesa: '🛡Защита' }
		});
	});

	it('черновик из приказов сервера и его ключ не зависят от порядка', () => {
		const draft = toDraft({ all: '🤖Hooli', corporations: { bmesa: '☂️Umbrella', piper: '🛡Защита' } });
		expect(draft).toEqual({ all: '🤖Hooli', corps: { bmesa: '☂️Umbrella', piper: '🛡Защита' } });
		expect(toDraft({ all: null, corporations: {} })).toEqual({ all: '', corps: {} });
		expect(draftKey({ all: '', corps: { b: 'x', a: 'y' } })).toBe(draftKey({ all: '', corps: { a: 'y', b: 'x' } }));
		// Пустая цель строки — то же, что её отсутствие.
		expect(draftKey({ all: '', corps: { a: '' } })).toBe(draftKey({ all: '', corps: {} }));
	});
});

describe('сводка по целям', () => {
	it('строка на цель: счёт, уровни, навыки, готовность', () => {
		const rows = summaryRows(first);
		expect(rows.map((r) => [r.label, r.count, r.levelSum, r.levelAvg, r.ready])).toEqual([
			['🤖Hooli', 1, 0, '—', 0],
			['☂️Umbrella', 2, 125, '62,5', 1],
			['🛡Защита', 1, 41, '41', 0]
		]);
		const umbrella = rows[1]!;
		expect(umbrella.skills).toEqual({ practice: 125, theory: 106, cunning: 62, wisdom: 48 });
		expect(umbrella.unknown).toBe(0);
		expect(rows[0]!.unknown).toBe(1);
		expect(umbrella.accountIds).toEqual([1, 2]);
	});

	it('аккаунты не под управлением бота — отдельная строка без цели', () => {
		const acc = first.accounts[0]!;
		const b: HqBattle = {
			...first,
			summary: [
				...first.summary,
				{ ...first.summary[0]!, target: null, count: 1, account_ids: [acc.id], level_sum: 9, level_avg: 9, ready: 0 }
			]
		};
		const last = summaryRows(b).at(-1)!;
		expect(last.target).toBeNull();
		expect(last.label).toBe('Цель не управляется ботом');
	});
});

describe('заголовок карточки', () => {
	it('ближайшая — с отсчётом, следующая — только с часом', () => {
		expect(battleTitle(first, true)).toBe('Ближайшая битва 13:00 (через 42 мин)');
		expect(battleTitle({ ...first, in_s: 7500 }, true)).toBe('Ближайшая битва 13:00 (через 2 ч 5 мин)');
		expect(battleTitle({ ...first, in_s: 20 }, true)).toBe('Ближайшая битва 13:00 (через 20 с)');
		expect(battleTitle(second, false)).toBe('Следующая 22:00');
	});
});

describe('тексты ошибок сохранения', () => {
	const companies: HqCompany[] = hq.companies;
	const validation = (type: string, loc: (string | number)[]): ApiError => ({
		kind: 'validation',
		status: 422,
		issues: [{ loc, msg: 'x', type }]
	});

	it('корпорация против самой себя — по названию компании', () => {
		expect(saveErrorText(validation('target_is_own_company', ['body', 'corporations', 'hooli']), companies)).toBe(
			'Корпорация «🤖Hooli» не может атаковать саму себя'
		);
	});

	it('прочие коды штаба', () => {
		expect(saveErrorText({ kind: 'conflict', status: 409, code: 'battle_too_close' }, companies)).toBe(
			'До битвы меньше минуты: приказ уже не применится'
		);
		expect(saveErrorText({ kind: 'invalid', status: 422, code: 'battle_not_offered' }, companies)).toBe(
			'Эта битва уже не предлагается: данные обновлены'
		);
		expect(saveErrorText(validation('unknown_target', ['body', 'all']), companies)).toBe(
			'Такой цели нет в списке: всем'
		);
		expect(saveErrorText(validation('unknown_target', ['body', 'corporations', 'zzz']), companies)).toBe(
			'Такой цели нет в списке: zzz'
		);
		expect(saveErrorText({ kind: 'network', status: 0, message: 'offline' }, companies)).toMatch(/\S/);
	});
});
