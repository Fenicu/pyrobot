import type { ScheduleItem, SharedScheduleItem } from '$lib/api/types';
import { artifactLabel } from '$lib/artifact/text';
import { deedText, WAKE } from '$lib/plan/text';

export interface MarkPosition {
	pos: number;
	ready: boolean;
}

export interface Segment {
	kind: string;
	detail: string | null;
	start: number;
	end: number;
	at: string;
	until: string;
}

export interface Tick {
	pos: number;
	label: string;
	hour: number;
	at: Date;
}

export interface Band {
	kind: string;
	label: string;
	start: number;
	end: number;
	at: string;
	until: string;
}

export interface SharedMark {
	kind: string;
	label: string;
	pos: number;
	at: string;
}

export interface RenderedMark {
	kind: string;
	detail: string | null;
	source: string;
	at: string;
	pos: number;
	ready: boolean;
}

/** Расчёт положения метки на шкале (0..1). Метки в прошлом прижимаются к 0 со статусом `ready`. */
export function position(at: Date | string, now: Date | string, hours: number): MarkPosition {
	const atMs = typeof at === 'string' ? new Date(at).getTime() : at.getTime();
	const nowMs = typeof now === 'string' ? new Date(now).getTime() : now.getTime();
	const totalMs = hours * 3600 * 1000;
	if (atMs <= nowMs) {
		return { pos: 0, ready: true };
	}
	const frac = (atMs - nowMs) / totalMs;
	return { pos: Math.min(1, Math.max(0, frac)), ready: false };
}

/** Отрезки занятости/сна (until != null), обрезанные по границам временного окна. */
export function segments(items: ScheduleItem[], now: Date | string, hours: number): Segment[] {
	const nowMs = typeof now === 'string' ? new Date(now).getTime() : now.getTime();
	const totalMs = hours * 3600 * 1000;
	const windowEnd = nowMs + totalMs;
	const res: Segment[] = [];

	for (const item of items) {
		if (!item.until) continue;
		const startMs = new Date(item.at).getTime();
		const endMs = new Date(item.until).getTime();
		if (endMs <= nowMs || startMs >= windowEnd) continue;

		const clippedStart = Math.max(nowMs, startMs);
		const clippedEnd = Math.min(windowEnd, endMs);
		if (clippedEnd <= clippedStart) continue;

		res.push({
			kind: item.kind,
			detail: item.detail ?? null,
			start: (clippedStart - nowMs) / totalMs,
			end: (clippedEnd - nowMs) / totalMs,
			at: item.at,
			until: item.until
		});
	}
	return res;
}

/** Засечки полных часов по Москве (UTC+3) в окне `[now, now + hours]`. */
export function hourTicks(now: Date | string, hours: number): Tick[] {
	const nowMs = typeof now === 'string' ? new Date(now).getTime() : now.getTime();
	const totalMs = hours * 3600 * 1000;
	const endMs = nowMs + totalMs;
	const MSK_OFFSET = 3 * 3600 * 1000;
	const HOUR_MS = 3600 * 1000;

	const nowMsk = nowMs + MSK_OFFSET;
	const firstHourMsk = Math.ceil(nowMsk / HOUR_MS) * HOUR_MS;

	const ticks: Tick[] = [];
	for (let tickMsk = firstHourMsk; tickMsk <= endMs + MSK_OFFSET; tickMsk += HOUR_MS) {
		const tickUtc = tickMsk - MSK_OFFSET;
		const pos = (tickUtc - nowMs) / totalMs;
		if (pos < 0 || pos > 1.0001) continue;
		const mskHour = Math.floor(tickMsk / HOUR_MS) % 24;
		const label = `${String(mskHour).padStart(2, '0')}:00`;
		ticks.push({
			pos: Math.min(1, Math.max(0, pos)),
			label,
			hour: mskHour,
			at: new Date(tickUtc)
		});
	}
	return ticks;
}

/** Фоновые полосы общих событий (ночь, фабрика, лотерея) с обрезкой по окну. */
export function sharedBands(shared: SharedScheduleItem[], now: Date | string, hours: number): Band[] {
	const nowMs = typeof now === 'string' ? new Date(now).getTime() : now.getTime();
	const totalMs = hours * 3600 * 1000;
	const windowEnd = nowMs + totalMs;
	const bands: Band[] = [];

	for (const s of shared) {
		if (!s.until) continue;
		const startMs = new Date(s.at).getTime();
		const endMs = new Date(s.until).getTime();
		if (endMs <= nowMs || startMs >= windowEnd) continue;

		const clippedStart = Math.max(nowMs, startMs);
		const clippedEnd = Math.min(windowEnd, endMs);
		if (clippedEnd <= clippedStart) continue;

		bands.push({
			kind: s.kind,
			label: s.label,
			start: (clippedStart - nowMs) / totalMs,
			end: (clippedEnd - nowMs) / totalMs,
			at: s.at,
			until: s.until
		});
	}
	return bands;
}

/** Точечные общие отметки (например, битва). */
export function sharedMarks(shared: SharedScheduleItem[], now: Date | string, hours: number): SharedMark[] {
	const nowMs = typeof now === 'string' ? new Date(now).getTime() : now.getTime();
	const totalMs = hours * 3600 * 1000;
	const windowEnd = nowMs + totalMs;
	const res: SharedMark[] = [];

	for (const s of shared) {
		if (s.until !== null) continue;
		const atMs = new Date(s.at).getTime();
		if (atMs >= nowMs && atMs <= windowEnd) {
			res.push({
				kind: s.kind,
				label: s.label,
				pos: (atMs - nowMs) / totalMs,
				at: s.at
			});
		}
	}
	return res;
}

/** Точечные метки аккаунта для отрисовки. */
export function accountMarks(items: ScheduleItem[], now: Date | string, hours: number): RenderedMark[] {
	const res: RenderedMark[] = [];
	for (const it of items) {
		if (it.until !== null) continue;
		const p = position(it.at, now, hours);
		res.push({
			kind: it.kind,
			detail: it.detail ?? null,
			source: it.source,
			at: it.at,
			pos: p.pos,
			ready: p.ready
		});
	}
	return res;
}

const STATE_ICONS: Record<string, { icon: string; text: string }> = {
	sleep_deadline: { icon: '🛌', text: 'Дедлайн сна' },
	sleep_allowed: { icon: '🛌', text: 'Можно лечь спать' },
	book_ready: { icon: '📒', text: 'Книга' },
	card_ready: { icon: '💳', text: 'Подарочная карта' },
	fastfood_ready: { icon: '🍔', text: 'Фастфуд' },
	prizebox_ready: { icon: '🎁', text: 'Призовая коробка' },
	tangerine_ready: { icon: '🍊', text: 'Мандарин' },
	metro_ready: { icon: '🚇', text: 'Метро' }
};

export type SchedulableItem =
	| ScheduleItem
	| Segment
	| RenderedMark
	| { kind: string; detail?: string | null; source?: string };

/** Подпись и иконка для метки или отрезка в строке аккаунта. */
export function itemText(item: SchedulableItem): {
	icon: string;
	text: string;
} {
	if (item.kind === 'busy') {
		const raw = item.detail ?? '';
		const key = raw.startsWith('deed:') ? raw : `deed:${raw}`;
		const translated = deedText(key);
		const deed = translated !== key ? translated : raw;
		return { icon: '⏳', text: deed ? `Занят: ${deed}` : 'Занят' };
	}
	if (item.kind === 'sleep') {
		const place =
			item.detail === 'sleep_hotel'
				? ' (отель)'
				: item.detail === 'sleep_bridge'
					? ' (под мостом)'
					: '';
		return { icon: '🛌', text: `Сон${place}` };
	}
	if (item.kind === 'artifact_end') {
		return { icon: '👾', text: item.detail ? `Артефакт: ${artifactLabel(item.detail)}` : 'Сбор артефакта' };
	}
	if (item.kind === 'lottery_close') {
		return { icon: '🤑', text: item.detail ? `Лотерея: тираж ${item.detail}` : 'Лотерея' };
	}
	if (item.kind in STATE_ICONS) {
		return STATE_ICONS[item.kind]!;
	}
	const wake = WAKE[item.kind as keyof typeof WAKE];
	if (wake) {
		const extra = item.detail ? ` (${item.detail})` : '';
		return { icon: wake.icon, text: `${wake.text}${extra}` };
	}
	return { icon: '⏱', text: item.kind };
}

/** Иконка и русское название для общих полос и отметок. */
export function sharedText(kind: string): { icon: string; title: string } {
	switch (kind) {
		case 'battle':
			return { icon: '⚔', title: 'Битва' };
		case 'factory':
			return { icon: '🏭', title: 'Фабрика' };
		case 'lottery':
			return { icon: '🤑', title: 'Лотерея' };
		case 'night':
			return { icon: '🌙', title: 'Ночь' };
		default:
			return { icon: '📍', title: kind };
	}
}
