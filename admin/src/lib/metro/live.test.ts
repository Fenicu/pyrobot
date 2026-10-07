import { describe, expect, it } from 'vitest';
import { liveFrame } from '$lib/test/metro-live';
import { endedAt, modeText, outcomeText, shown } from './live';

const at = (iso: string) => Date.parse(iso);

describe('живой кадр метро: тексты и показ', () => {
	it('режим по-русски, у ухода — причина', () => {
		expect(modeText(liveFrame())).toBe('обход');
		expect(modeText(liveFrame({ mode: 'frontier' }))).toBe('ищу выход');
		expect(modeText(liveFrame({ mode: 'leave', leave_reason: 'deadline' }))).toBe('к выходу: не успеть до битвы');
		expect(modeText(liveFrame({ mode: 'leave', leave_reason: 'explored' }))).toBe('к выходу: всё обошёл');
		expect(modeText(liveFrame({ mode: 'leave', leave_reason: 'no_stamina' }))).toBe('к выходу: 🔋 0 и нет аптечек');
		expect(modeText(liveFrame({ mode: 'leave', leave_reason: 'later' }))).toBe('к выходу: later');
		expect(modeText(liveFrame({ mode: 'what' }))).toBe('what');
	});

	it('исход: вышел сам, выброс, остановка с причиной', () => {
		const end = { running: false };
		expect(outcomeText(liveFrame({ ...end, outcome: 'finished', mode: 'leave' }))).toBe('вышел сам');
		expect(outcomeText(liveFrame({ ...end, outcome: 'finished', mode: 'explore' }))).toBe('выброс');
		expect(outcomeText(liveFrame({ ...end, outcome: 'paused' }))).toBe('остановлен: пауза');
		expect(outcomeText(liveFrame({ ...end, outcome: 'unexpected_screen:fight' }))).toBe('остановлен: незнакомый экран');
		expect(outcomeText(liveFrame({ ...end, outcome: 'zzz' }))).toBe('остановлен: zzz');
	});

	it('идущий забег виден всегда, итог — 30 минут после конца', () => {
		const run = liveFrame();
		expect(shown(run, at('2026-10-07T18:10:00Z'), at('2026-10-07T23:00:00Z'))).toBe(true);
		const done = liveFrame({ running: false, outcome: 'finished', mode: 'leave' });
		// Кадр конца пришёл потоком в 18:40 — конец тогда.
		expect(endedAt(done, at('2026-10-07T18:40:00Z'))).toBe(at('2026-10-07T18:40:00Z'));
		expect(shown(done, at('2026-10-07T18:40:00Z'), at('2026-10-07T19:09:00Z'))).toBe(true);
		expect(shown(done, at('2026-10-07T18:40:00Z'), at('2026-10-07T19:11:00Z'))).toBe(false);
		// Кадр конца взят GET-ом позже: забег кончился не позже kick_at (18:45).
		expect(endedAt(done, at('2026-10-07T21:00:00Z'))).toBe(at('2026-10-07T18:45:00Z'));
		// Без битвы — не позже 90 мин от начала забега.
		const blind = liveFrame({ running: false, outcome: 'paused', kick_at: null, battle_at: null });
		expect(endedAt(blind, at('2026-10-08T09:00:00Z'))).toBe(at('2026-10-07T19:30:00Z'));
	});
});
