import { afterEach, describe, expect, it } from 'vitest';
import type { AccountOut, OverviewAccount } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import { accountOut } from '$lib/test/overview';
import {
	artifactText,
	artifactTitle,
	battleMismatch,
	loadSort,
	mergeRows,
	metroText,
	nextActionText,
	nextSort,
	rowProblems,
	saveSort,
	SORT_KEY,
	sortRows,
	totalsRow,
	type OverviewRow
} from './table';

const extras = fixture<OverviewAccount[]>('overview_accounts');
const [e1, e2, e3, e4] = extras as [OverviewAccount, OverviewAccount, OverviewAccount, OverviewAccount];

const accounts: AccountOut[] = [
	accountOut(1, 'Альфа', { level: 31, company: 'hooli' }),
	accountOut(2, 'Бета', { level: 12, busy: { activity: 'job', until: '2026-10-17T09:30:00Z' } }),
	accountOut(3, 'Гамма', { level: 4, busy: { activity: 'learn', until: '2026-10-17T10:10:00Z' } }),
	accountOut(4, 'Дельта')
];

const rows = (): OverviewRow[] => mergeRows(accounts, extras);
const ids = (list: OverviewRow[]) => list.map((r) => r.account.id);

describe('mergeRows', () => {
	it('соединяет по id и держит порядок аккаунтов', () => {
		const merged = mergeRows([accounts[2]!, accounts[0]!], extras);
		expect(ids(merged)).toEqual([3, 1]);
		expect(merged[0]!.extra).toBe(e3);
		expect(merged[1]!.extra).toBe(e1);
	});

	it('аккаунт без сводки (только что создан) остаётся строкой с extra: null', () => {
		const merged = mergeRows(accounts, [e1]);
		expect(ids(merged)).toEqual([1, 2, 3, 4]);
		expect(merged[1]!.extra).toBeNull();
	});

	it('сводка без аккаунта (удалён между опросами) строки не даёт', () => {
		expect(ids(mergeRows([accounts[0]!], extras))).toEqual([1]);
	});
});

describe('battleMismatch', () => {
	it('выставлено не то, что нужно: расхождение', () => {
		expect(battleMismatch(rows()[1]!)).toBe(true);
	});
	it('совпадает, неизвестно или битвы нет: расхождения нет', () => {
		expect(battleMismatch(rows()[0]!)).toBe(false);
		expect(battleMismatch(rows()[2]!)).toBe(false);
		expect(battleMismatch(rows()[3]!)).toBe(false);
	});
	it('без сводки расхождения нет', () => {
		expect(battleMismatch({ account: accounts[0]!, extra: null })).toBe(false);
	});
});

describe('sortRows', () => {
	it('ключ null: порядок как был', () => {
		expect(ids(sortRows(rows(), null, 'desc'))).toEqual([1, 2, 3, 4]);
	});

	it('по имени, по возрастанию и убыванию', () => {
		const shuffled = [rows()[2]!, rows()[0]!, rows()[3]!, rows()[1]!];
		expect(ids(sortRows(shuffled, 'name', 'asc'))).toEqual([1, 2, 3, 4]);
		expect(ids(sortRows(shuffled, 'name', 'desc'))).toEqual([4, 3, 2, 1]);
	});

	it('по уровню; без уровня - в конец при любом направлении', () => {
		expect(ids(sortRows(rows(), 'level', 'asc'))).toEqual([3, 2, 1, 4]);
		expect(ids(sortRows(rows(), 'level', 'desc'))).toEqual([1, 2, 3, 4]);
	});

	it('по доле опыта: exp / exp_next', () => {
		// 1240/5000 = 0.248; 300/900 = 0.33; 50/400 = 0.125
		expect(ids(sortRows(rows(), 'expShare', 'asc'))).toEqual([3, 1, 2, 4]);
		expect(ids(sortRows(rows(), 'expShare', 'desc'))).toEqual([2, 1, 3, 4]);
	});

	it.each([
		['money', [3, 2, 1, 4]],
		['stamina', [2, 1, 3, 4]],
		['motivation', [2, 1, 3, 4]]
	] as const)('по %s', (key, asc) => {
		expect(ids(sortRows(rows(), key, 'asc'))).toEqual(asc);
		const desc = [...asc.slice(0, -1)].reverse();
		expect(ids(sortRows(rows(), key, 'desc'))).toEqual([...desc, 4]);
	});

	it('по концу занятости; свободные (без busy) в конце', () => {
		expect(ids(sortRows(rows(), 'busyUntil', 'asc'))).toEqual([2, 3, 1, 4]);
		expect(ids(sortRows(rows(), 'busyUntil', 'desc'))).toEqual([3, 2, 1, 4]);
	});

	it('по готовности цели: не выставлена, неизвестно, выставлена; без битвы в конце', () => {
		expect(ids(sortRows(rows(), 'battleReady', 'asc'))).toEqual([2, 3, 1, 4]);
		expect(ids(sortRows(rows(), 'battleReady', 'desc'))).toEqual([1, 3, 2, 4]);
	});

	it('по числу проблем', () => {
		const sorted = sortRows(rows(), 'problems', 'desc');
		expect(ids(sorted)[0]).toBe(2);
		expect(ids(sortRows(rows(), 'problems', 'asc'))[0]).not.toBe(2);
	});

	it('равные ключи держат исходный порядок и вход не меняется', () => {
		const input = rows();
		const sorted = sortRows(input, 'name', 'asc');
		expect(ids(input)).toEqual([1, 2, 3, 4]);
		expect(sorted).not.toBe(input);
		const same = mergeRows(
			[accountOut(7, 'Я', { level: 5 }), accountOut(5, 'Я', { level: 5 }), accountOut(6, 'Я', { level: 5 })],
			[]
		);
		expect(ids(sortRows(same, 'level', 'asc'))).toEqual([7, 5, 6]);
	});
});

describe('totalsRow', () => {
	it('суммы по строкам, null пропускаются', () => {
		expect(totalsRow(rows())).toEqual({ money: 24450, knowledge: 322, details: 45, raw: 22 });
	});
	it('пустой список и строки без сводки дают нули', () => {
		expect(totalsRow([])).toEqual({ money: 0, knowledge: 0, details: 0, raw: 0 });
		expect(totalsRow([{ account: accounts[0]!, extra: null }])).toEqual({ money: 0, knowledge: 0, details: 0, raw: 0 });
	});
});

describe('nextActionText', () => {
	it('действие: подпись сценария', () => {
		expect(nextActionText(e1.next_action!)).toEqual({ text: '⛏ добыча', at: null });
	});
	it('ожидание: причина-таймер и срок', () => {
		expect(nextActionText(e2.next_action!)).toEqual({ text: '⏳ Освободится', at: '2026-10-17T09:40:00+00:00' });
	});
	it('ожидание с ключом таймера берёт вид до двоеточия', () => {
		const text = nextActionText({ kind: 'wait', scenario: null, reason: 'cooldown:metro', until: null, blocked_by: null });
		expect(text.text).toBe('⏳ Кончится отсрочка');
	});
	it('нет таймеров и незнакомая причина', () => {
		expect(nextActionText({ kind: 'wait', scenario: null, reason: 'no_timers', until: null, blocked_by: null }).text).toBe(
			'⏳ ждёт событий'
		);
		expect(nextActionText({ kind: 'wait', scenario: null, reason: 'zzz', until: null, blocked_by: null }).text).toBe('⏳ zzz');
	});
});

describe('rowProblems', () => {
	it('здоровая строка без проблем', () => {
		expect(rowProblems(rows()[0]!)).toEqual([]);
	});

	it('устаревший снимок, пауза цикла и невыставленная цель', () => {
		const problems = rowProblems(rows()[1]!);
		expect(problems).toContain('снимок устарел');
		expect(problems).toContain('пауза');
		expect(problems.some((p) => p.startsWith('цель:'))).toBe(true);
	});

	it('предупреждение аккаунта идёт первым', () => {
		const row: OverviewRow = {
			account: accountOut(1, 'x', { alert: { level: 'error', text: 'нет денег' } }),
			extra: e1
		};
		expect(rowProblems(row)[0]).toBe('нет денег');
	});

	it('выключенный аккаунт не считается проблемным за остановленный движок', () => {
		const row: OverviewRow = { account: accountOut(3, 'x', { status: 'disabled' }), extra: e3 };
		expect(rowProblems(row)).toEqual([]);
	});

	it('включённый аккаунт с остановленным движком: цель не дойдёт', () => {
		const problems = rowProblems(rows()[2]!);
		expect(problems).toContain('движок не запущен');
	});
});

describe('nextSort', () => {
	it('новый ключ: по возрастанию, повтор: по убыванию, третий: сброс', () => {
		let s = nextSort({ key: null, dir: 'asc' }, 'money');
		expect(s).toEqual({ key: 'money', dir: 'asc' });
		s = nextSort(s, 'money');
		expect(s).toEqual({ key: 'money', dir: 'desc' });
		s = nextSort(s, 'money');
		expect(s).toEqual({ key: null, dir: 'asc' });
	});
	it('другой ключ начинает с возрастания', () => {
		expect(nextSort({ key: 'money', dir: 'desc' }, 'level')).toEqual({ key: 'level', dir: 'asc' });
	});
});

describe('сохранённая сортировка', () => {
	afterEach(() => localStorage.clear());

	it('возвращается как была записана', () => {
		saveSort({ key: 'stamina', dir: 'desc' });
		expect(loadSort()).toEqual({ key: 'stamina', dir: 'desc' });
	});
	it('мусор в хранилище - порядок пользователя', () => {
		localStorage.setItem(SORT_KEY, '{"key":"nope","dir":"up"}');
		expect(loadSort()).toEqual({ key: null, dir: 'asc' });
		localStorage.setItem(SORT_KEY, 'не json');
		expect(loadSort()).toEqual({ key: null, dir: 'asc' });
	});
	it('сброс очищает запись', () => {
		saveSort({ key: 'money', dir: 'asc' });
		saveSort({ key: null, dir: 'asc' });
		expect(localStorage.getItem(SORT_KEY)).toBeNull();
	});
});

describe('метро и артефакт', () => {
	const now = new Date('2026-10-17T09:18:00Z');
	it('идущий забег важнее срока', () => {
		const row = { account: accountOut(1, 'x', { in_metro: true }), extra: e1 };
		expect(metroText(row, now)).toBe('идёт');
	});
	it('срок в будущем - когда, прошедший - готово, нет - прочерк', () => {
		expect(metroText(rows()[0]!, now)).toBe('в 15:10');
		expect(metroText(rows()[0]!, new Date('2026-10-17T13:00:00Z'))).toBe('готово');
		expect(metroText(rows()[1]!, now)).toBe('—');
		expect(metroText({ account: accounts[0]!, extra: null }, now)).toBe('—');
	});
	it('артефакт: до какого момента идёт сбор', () => {
		expect(artifactText(e1, now)).toBe('до 19.10 12:00');
		expect(artifactText(e2, now)).toBe('—');
		expect(artifactText(e1, new Date('2026-10-20T00:00:00Z'))).toBe('завершён');
		expect(artifactText(null, now)).toBe('—');
	});
	it('артефакт: название вместо кода во всплывающей подсказке', () => {
		expect(artifactTitle(e1)).toBe('🔦 Фонарь Sw-ет');
		expect(artifactTitle(e2)).toBeUndefined();
		expect(artifactTitle(null)).toBeUndefined();
	});
});
