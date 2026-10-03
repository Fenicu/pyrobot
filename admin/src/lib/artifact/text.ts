/** Сбор артефакта: названия, дела, статусы — как в игре и в `app/engine/artifact.py`. */
import type { ArtifactOut } from '$lib/api/types';
import { fmtSpan } from '$lib/util/format';
import { ACTIVITY } from '$lib/util/game';

export type ArtifactKey = 'book' | 'fax' | 'light';
export const RECOLLECT: ArtifactKey[] = ['book', 'fax', 'light'];

export const ARTIFACT: Record<ArtifactKey, { icon: string; name: string }> = {
	book: { icon: '📕', name: 'Букварь стартапера' },
	fax: { icon: '📠', name: 'SW факs' },
	light: { icon: '🔦', name: 'Фонарь Sw-ет' }
};

function known(key: string | null | undefined): key is ArtifactKey {
	return key === 'book' || key === 'fax' || key === 'light';
}

export function artifactIcon(key: string | null | undefined): string {
	return known(key) ? ARTIFACT[key].icon : '👾';
}

/** «🔦 Фонарь Sw-ет»; неизвестный — как есть. */
export function artifactLabel(key: string | null | undefined): string {
	return known(key) ? `${ARTIFACT[key].icon} ${ARTIFACT[key].name}` : (key ?? '—');
}

/** Дела тактики по приоритету: «🔥 → прогулка → работа». */
export function deedsLine(deeds: string[]): string {
	return ['🔥', ...deeds.map((d) => ACTIVITY[d] ?? d)].join(' → ');
}

export const RUN_STATUS: Record<ArtifactOut['run']['status'], string> = {
	idle: 'нет сбора',
	starting: 'запуск',
	active: 'идёт',
	paused: 'пауза',
	cancelled: 'отменён',
	finished: 'завершён'
};

/** «осталось 8 д»; срок прошёл — «срок вышел». */
export function leftText(endsAt: string | null, now: Date): string {
	if (!endsAt) return '';
	const ms = new Date(endsAt).getTime() - now.getTime();
	return ms > 0 ? `осталось ${fmtSpan(ms / 1000)}` : 'срок вышел';
}
