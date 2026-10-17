import { errorText, type ApiError } from '$lib/api/errors';
import type { BattleOrders, BattleOrdersIn, HqBattle, HqCompany, HqTarget } from '$lib/api/types';
import { fmtSpan, fmtTime } from '$lib/util/format';

/** Цели строки корпорации: её собственная компания убрана (в меню игры такой кнопки нет), защита
 * остается всегда. `null` — приказ «всем»: любая цель. */
export function allowedTargets(targets: HqTarget[], ownCompanyCode: string | null): HqTarget[] {
	return ownCompanyCode === null ? targets : targets.filter((t) => t.code !== ownCompanyCode);
}

export interface BattleWarnings {
	tooLate: boolean;
	/** Аккаунты, чья цель по приказу — их собственная компания: уйдут в защиту. */
	defense: number;
	/** Аккаунты, до которых приказ не дойдёт, по причинам (в порядке появления). */
	unmanaged: { reason: string; count: number }[];
}

export function battleWarnings(b: HqBattle): BattleWarnings {
	const reasons = new Map<string, number>();
	for (const a of b.accounts) {
		// Слишком позднее время уже названо отдельным предупреждением.
		if (a.applies || a.note === null || a.note === 'too_late') continue;
		reasons.set(a.note, (reasons.get(a.note) ?? 0) + 1);
	}
	return {
		tooLate: !b.editable,
		defense: b.accounts.filter((a) => a.note === 'own_company').length,
		unmanaged: [...reasons].map(([reason, count]) => ({ reason, count }))
	};
}

const NOTE: Record<string, string> = {
	paused: 'на паузе',
	killed: 'остановлен (kill)',
	manual_control: 'ручное управление',
	tg_offline: 'Telegram не в сети',
	tg_unreachable: 'нет связи с Telegram',
	pipeline_unhealthy: 'конвейер не в порядке',
	lock_lost: 'аренда потеряна',
	bulls_walk: 'идёт обход быков',
	spending_blocked: 'траты заблокированы',
	auto_off: 'планировщик не принимает решений',
	scenario_running: 'идёт сценарий: цель после него',
	engine_stopped: 'движок не запущен',
	engine_elsewhere: 'движок на другом хосте',
	dry_run: 'режим dry_run',
	feature_off: 'битва выключена в настройках',
	company_unknown: 'компания аккаунта неизвестна',
	own_company: 'своя компания: защита',
	too_late: 'слишком поздно'
};

/** Причина «приказ не дойдёт» словами; незнакомый код — как есть. */
export function noteText(code: string): string {
	return NOTE[code] ?? code;
}

/** Склонение по числу: 1 аккаунт, 2 аккаунта, 5 аккаунтов. */
function form(n: number, one: string, few: string, many: string): string {
	const tail = n % 100;
	if (tail >= 11 && tail <= 14) return many;
	const last = n % 10;
	return last === 1 ? one : last >= 2 && last <= 4 ? few : many;
}

export function warningLines(b: HqBattle): string[] {
	const w = battleWarnings(b);
	const lines: string[] = [];
	if (w.tooLate) lines.push('до битвы меньше минуты: приказ уже не применится');
	if (w.defense > 0) {
		const n = w.defense;
		lines.push(
			n === 1
				? '1 аккаунт пойдёт в 🛡Защиту: цель — его компания'
				: `${n} ${form(n, 'аккаунт', 'аккаунта', 'аккаунтов')} пойдут в 🛡Защиту: цель — их компания`
		);
	}
	for (const { reason, count } of w.unmanaged) {
		const verb = form(count, 'не поставит', 'не поставят', 'не поставят');
		lines.push(`${count} ${form(count, 'аккаунт', 'аккаунта', 'аккаунтов')} цель ${verb}: ${noteText(reason)}`);
	}
	return lines;
}

/** Правка формы: цель «всем» (`''` — не задавать) и по корпорациям (код → цель). */
export interface OrdersDraft {
	all: string;
	corps: Record<string, string>;
}

export function toDraft(orders: BattleOrders): OrdersDraft {
	return { all: orders.all ?? '', corps: { ...orders.corporations } };
}

/** Ключ для сравнения приказов: пустая цель — то же, что её отсутствие, порядок не важен. */
export function draftKey(d: OrdersDraft): string {
	const corps = Object.entries(d.corps)
		.filter(([, target]) => target !== '')
		.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
	return JSON.stringify([d.all, corps]);
}

export function ordersKey(orders: BattleOrders): string {
	return draftKey(toDraft(orders));
}

/** Тело `PUT`: пустые цели не отправляются; пустое тело — отмена приказов. */
export function ordersBody(all: string | null, corps: Record<string, string | null>): BattleOrdersIn {
	const corporations: Record<string, string> = {};
	for (const [code, target] of Object.entries(corps)) {
		if (target) corporations[code] = target;
	}
	return { all: all || null, corporations };
}

export function draftBody(d: OrdersDraft): BattleOrdersIn {
	return ordersBody(d.all, d.corps);
}

export interface SummaryRow {
	/** null — аккаунты, чью цель бот не ставит вовсе. */
	target: string | null;
	label: string;
	count: number;
	levelSum: number;
	levelAvg: string;
	unknown: number;
	skills: { practice: number; theory: number; cunning: number; wisdom: number };
	ready: number;
	accountIds: number[];
}

const avg = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });

export function summaryRows(b: HqBattle): SummaryRow[] {
	return b.summary.map((s) => ({
		target: s.target,
		label: s.target ?? 'Цель не управляется ботом',
		count: s.count,
		levelSum: s.level_sum,
		levelAvg: s.level_avg === null ? '—' : avg.format(s.level_avg),
		unknown: s.unknown,
		skills: s.skills,
		ready: s.ready,
		accountIds: s.account_ids
	}));
}

/** «Ближайшая битва 13:00 (через 42 мин)» / «Следующая 22:00». */
export function battleTitle(b: HqBattle, nearest: boolean): string {
	const at = fmtTime(b.battle_at);
	return nearest ? `Ближайшая битва ${at} (через ${fmtSpan(b.in_s)})` : `Следующая ${at}`;
}

/** Текст ошибки сохранения приказов. */
export function saveErrorText(err: ApiError, companies: HqCompany[]): string {
	if (err.kind === 'conflict' && err.code === 'battle_too_close') {
		return 'До битвы меньше минуты: приказ уже не применится';
	}
	if (err.kind === 'invalid' && err.code === 'battle_not_offered') {
		return 'Эта битва уже не предлагается: данные обновлены';
	}
	if (err.kind === 'validation') {
		const lines = err.issues.map((i) => {
			const where = i.loc.slice(1);
			const code = String(where[1] ?? '');
			if (i.type === 'target_is_own_company') {
				const name = companies.find((c) => c.code === code)?.name ?? code;
				return `Корпорация «${name}» не может атаковать саму себя`;
			}
			if (i.type === 'unknown_target') {
				return `Такой цели нет в списке: ${where[0] === 'all' ? 'всем' : code}`;
			}
			if (i.type === 'unknown_company') return `Неизвестная корпорация: ${code}`;
			return `${where.join('.')}: ${i.msg}`;
		});
		if (lines.length > 0) return lines.join('\n');
	}
	return errorText(err);
}
