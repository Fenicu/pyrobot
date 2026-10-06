/** Гаджеты при персонаже: слоты, сеты, строки задачи заточки и покупки — как в игре и в
 * `app/engine/gadgets.py`. */
import { ApiFailure } from '$lib/api/errors';
import type { GadgetSetKey, GadgetTarget, GadgetsOut, UpSlotKey, UpgradeChoice } from '$lib/api/types';
import { fmtNum } from '$lib/util/format';

export const SLOT_ICON: Record<UpSlotKey, string> = {
	right: '📱',
	left: '⌚️',
	legs: '👞',
	head: '🕶',
	chest: '👕',
	torso: '👔',
	ring: '💍',
	book: '💻',
	pbank: '🪫',
	pants: '👖'
};

export const SET_TITLE: Record<GadgetSetKey, { icon: string; name: string }> = {
	summer: { icon: '🌞', name: 'Летний' },
	autumn: { icon: '🍂', name: 'Осень' },
	um: { icon: '', name: 'Um-сет' },
	pig: { icon: '🐷', name: 'Свинтус' },
	y2020: { icon: '🆘', name: '2020' },
	spring: { icon: '🌸', name: 'Весенний' },
	logistic: { icon: '🗳', name: 'Логистик' }
};

/** «🍂 Осень», «Um-сет». */
export function setTitle(key: GadgetSetKey): string {
	const t = SET_TITLE[key];
	return t.icon ? `${t.icon} ${t.name}` : t.name;
}

export const KIND_MARK: Record<UpgradeChoice, string> = { white: '⚪️', blue: '🔵', red: '🔴', auto: 'авто' };
export const UPGRADE_KINDS: UpgradeChoice[] = ['white', 'blue', 'red', 'auto'];
export const SHOP_PARTS = 6;
export const MAX_LEVEL = 60;
export const DEFAULT_TARGET = 25;
export const BAG_FULL = 'рюкзак полон — покупка на паузе';

/** Провал попытки на уровне N снимает ⌊N/4⌋ уровней. */
export function lossOnFail(level: number): number {
	return Math.floor(level / 4);
}

/** Цель по умолчанию: 25, а гаджет уже выше — следующий уровень. */
export function defaultTarget(level: number): number {
	return Math.min(MAX_LEVEL, Math.max(DEFAULT_TARGET, level + 1));
}

const money = (n: number | null) => (n === null ? '$?' : `$${fmtNum(n)}`);

function progressTail(out: GadgetsOut): string {
	const p = out.progress;
	if (!p) return '';
	return ` · попыток ${p.attempts} · ✓${p.ok} ✗${p.fail} · ⚪️${p.spent.white} 🔵${p.spent.blue} 🔴${p.spent.red}`;
}

function taskGadget(out: GadgetsOut): string {
	const t = out.task;
	const icon = t.slot ? SLOT_ICON[t.slot] : '';
	return `${icon} ${t.gadget ?? '?'}`.trim();
}

/** «Точим 📱 S-март: 🔴18 → цель 25 (авто) · попыток 37 · ✓29 ✗8 · ⚪️0 🔵4 🔴33»; не идёт — null. */
export function taskLine(out: GadgetsOut): string | null {
	const t = out.task;
	if (t.status !== 'active') return null;
	const worn = out.worn.find((g) => g.up_slot === t.slot && g.name === t.gadget);
	const level = t.level === null ? '?' : `${worn?.grade ?? ''}${t.level}`;
	const kind = t.kind ? ` (${KIND_MARK[t.kind]})` : '';
	return `Точим ${taskGadget(out)}: ${level} → цель ${t.target ?? '?'}${kind}${progressTail(out)}`;
}

/** Итог прошлой задачи — до следующего старта; остановленная вручную и не было задачи — null. */
export function resultLine(out: GadgetsOut): string | null {
	const t = out.task;
	const level = t.end_level ?? '?';
	switch (t.status) {
		case 'done':
			return `Заточено: ${taskGadget(out)} — ${level} ур., цель ${t.target} достигнута${progressTail(out)}`;
		case 'exhausted':
			return `Улучшения кончились: ${taskGadget(out)} — ${level} ур. при цели ${t.target}${progressTail(out)}`;
		case 'failed':
			return `Заточка прервана: ${taskGadget(out)} больше не надет${progressTail(out)}`;
		default:
			return null;
	}
}

/** «доступно $40 120 (наличные $1 120 + акции $40 000 − резерв $1 000)». */
export function moneyLine(m: NonNullable<GadgetsOut['buy']['money']>): string {
	return `доступно ${money(m.available)} (наличные ${money(m.cash)} + акции ${money(m.stocks)} − резерв ${money(m.reserve)})`;
}

/** Строка цели покупки: копим, покупаем, надеваем; у цели не в работе — null. */
export function buyLine(out: GadgetsOut): string | null {
	const plan = out.buy.plan;
	const t = plan?.target;
	if (!plan || !t) return null;
	const bought = `куплено ${SHOP_PARTS - t.missing.length}/${SHOP_PARTS}`;
	const title = setTitle(t.set);
	const m = out.buy.money;
	if (t.status === 'saving') {
		const funds = m ? `, ${moneyLine(m)}` : '';
		return `Копим на ${title}: ${bought}, на следующую часть не хватает $${fmtNum(t.need_money)}${funds}`;
	}
	if (t.status === 'ready') {
		const next = t.missing[0];
		const part = next ? `, следующая часть — ${SLOT_ICON[next.slot]} тир ${next.tier} за $${fmtNum(next.price)}` : '';
		return `Покупаем ${title}: ${bought}${part}`;
	}
	if (t.status === 'wearing') return `${title}: все части куплены — бот наденет сет`;
	return null;
}

/** Цель не в работе: заблокирована, надет без строки сета, не подтверждена, не по уровню. */
export function targetLine(t: GadgetTarget): string | null {
	const title = setTitle(t.set);
	switch (t.status) {
		case 'blocked': {
			const slots = t.blocked_by.map((s) => SLOT_ICON[s]).join(' или ');
			return `${title}: нужен ${slots} сета ${SET_TITLE[t.set].name} или выше`;
		}
		case 'worn_inactive':
			return `${title}: надет, сет не появился`;
		case 'unconfirmed':
			return `${title}: надет, активация не подтверждена`;
		case 'level':
			return `${title}: части сета не по уровню персонажа`;
		default:
			return null;
	}
}

const RULE: Record<string, string> = { empty: 'на пустой слот', set: 'часть сета', replace: 'замена надетого' };

/** Ближайший шаг покупки: что купит или наденет бот; без шага — null. */
export function actionLine(out: GadgetsOut): string | null {
	const action = out.buy.plan?.action;
	if (!action) return null;
	if (action.type === 'wear_set') return `Бот наденет сет ${setTitle(action.set)}`;
	const what = `${SLOT_ICON[action.slot]} тир ${action.tier}`;
	if (action.in_bag) return `Бот наденет из рюкзака: ${what} (${RULE[action.rule]})`;
	const sell = action.sell_needed > 0 ? `, продав акции на $${fmtNum(action.sell_needed)}` : '';
	return `Следующая покупка: ${what} за $${fmtNum(action.price)} (${RULE[action.rule]})${sell}`;
}

/** Рюкзак полон по экрану `/inv` или по вердикту плана. */
export function bagFull(out: GadgetsOut): boolean {
	const { used, cap } = out.bag;
	return out.buy.plan?.verdict === 'bag_full' || (used !== null && cap !== null && used >= cap);
}

/** Тексты 409 заточки: общий `dry_run` говорит про сбор артефакта. */
export const UPGRADE_ERRORS: Record<string, string> = {
	dry_run: 'Заточка — только в режиме live',
	upgrade_in_progress: 'Заточка уже идёт — сначала остановите её',
	not_worn: 'На этом слоте сейчас ничего не надето',
	target_reached: 'Цель не выше нынешнего уровня гаджета',
	no_task: 'Заточка не идёт — нечего останавливать'
};

export function upgradeError(e: unknown): string {
	if (e instanceof ApiFailure) {
		const err = e.error;
		const known = err.kind === 'conflict' ? UPGRADE_ERRORS[err.code] : undefined;
		return known ?? e.message;
	}
	return String(e);
}
