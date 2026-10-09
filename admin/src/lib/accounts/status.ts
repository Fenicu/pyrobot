import type { AccountOut } from '$lib/api/types';
import { activityLabel } from '$lib/util/game';

export type Tone = 'ok' | 'warn' | 'bad' | 'off';

/** Подпись точки статуса для чтеца и подсказки. */
export const TONE_LABEL: Record<Tone, string> = {
	ok: 'работает',
	warn: 'требует внимания',
	bad: 'ошибка',
	off: 'выключен'
};

/** Тон точки статуса: первое подходящее правило. */
export function accountTone(a: AccountOut): Tone {
	if (a.status === 'disabled' || a.status === 'deleting') return 'off';
	if (a.status === 'error' || a.blocked || a.killed || a.unread.error > 0) return 'bad';
	if (a.paused || a.unread.warn > 0 || !a.tg.online) return 'warn';
	return 'ok';
}

export function needsAttention(a: AccountOut): boolean {
	const tone = accountTone(a);
	return tone === 'bad' || tone === 'warn';
}

export interface Activity {
	text: string;
	until: string | null;
	tone: 'muted' | 'warn' | 'bad';
}

const say = (text: string, tone: Activity['tone'] = 'muted', until: string | null = null): Activity => ({
	text,
	until,
	tone
});

/** Подпись состояния; предупреждение показывает только карточка, колонка — само состояние.
 * Занятость, которая кончилась по часам страницы, — «свободен»: снимок обновится с опросом. */
export function accountActivity(a: AccountOut, now: Date, opts: { card?: boolean } = {}): Activity {
	if (a.status === 'deleting') return say('удаляется');
	if (a.status === 'disabled') return say('выключен');
	if (a.blocked) return say(a.blocked_reason ?? 'заблокирован', 'bad');
	if (a.status === 'error') return say(a.status_reason ?? 'ошибка', 'bad');
	if (opts.card && a.alert) return say(a.alert.text, a.alert.level === 'error' ? 'bad' : 'warn');
	if (a.killed) return say('kill', 'bad');
	if (a.mode === 'dry_run') return say('dry_run');
	if (a.paused) return say('пауза', 'warn');
	if (a.in_metro) return say('метро');
	if (a.busy && Date.parse(a.busy.until) > now.getTime()) {
		return say(activityLabel(a.busy.activity), 'muted', a.busy.until);
	}
	return say('свободен');
}
