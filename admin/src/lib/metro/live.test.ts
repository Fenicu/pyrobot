import { describe, expect, it } from 'vitest';
import { liveFrame } from '$lib/test/metro-live';
import { endedAt, lostAt, modeText, outcomeText, shown } from './live';

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

	it('итог — 30 минут после конца: конец по доле бюджета, без бюджета — по получению кадра', () => {
		// Доля бюджета на конец точна: 18:00 + 0.75 × 40 мин = 18:30, когда бы кадр ни пришёл.
		const done = liveFrame({ running: false, outcome: 'finished', mode: 'leave', budget: { total_s: 2400, used: 0.75, step_s: 5 } });
		expect(endedAt(done, at('2026-10-07T21:00:00Z'))).toBe(at('2026-10-07T18:30:00Z'));
		expect(shown(done, at('2026-10-07T18:30:00Z'), at('2026-10-07T18:59:00Z'))).toBe(true);
		expect(shown(done, at('2026-10-07T18:30:00Z'), at('2026-10-07T19:01:00Z'))).toBe(false);
		// Бюджет исчерпан (доля упёрлась в 1) или неизвестен — момент получения, но не позже kick_at…
		const late = liveFrame({ running: false, outcome: 'finished', budget: { total_s: 2400, used: 1, step_s: 5 } });
		expect(endedAt(late, at('2026-10-07T18:42:00Z'))).toBe(at('2026-10-07T18:42:00Z'));
		expect(endedAt(late, at('2026-10-07T21:00:00Z'))).toBe(at('2026-10-07T18:45:00Z'));
		// …и не позже 90 мин от начала забега.
		const blind = liveFrame({
			running: false,
			outcome: 'paused',
			kick_at: null,
			battle_at: null,
			budget: { total_s: null, used: 0, step_s: 5 }
		});
		expect(endedAt(blind, at('2026-10-08T09:00:00Z'))).toBe(at('2026-10-07T19:30:00Z'));
	});

	it('идущий кадр: связь потеряна через 5 мин без кадров или через 2 мин после выброса, ещё через 30 — не виден', () => {
		const run = liveFrame();
		const got = at('2026-10-07T18:10:00Z');
		expect(lostAt(run, got)).toBe(at('2026-10-07T18:15:00Z'));
		expect(shown(run, got, at('2026-10-07T18:15:00Z'))).toBe(true);
		expect(shown(run, got, at('2026-10-07T18:45:00Z'))).toBe(true);
		expect(shown(run, got, at('2026-10-07T18:45:01Z'))).toBe(false);
		// Кадр свежий, но выброс (18:45) прошёл больше 2 минут назад.
		expect(lostAt(run, at('2026-10-07T18:44:00Z'))).toBe(at('2026-10-07T18:47:00Z'));
		expect(lostAt(liveFrame({ running: false, outcome: 'finished' }), got)).toBeNull();
	});
});
