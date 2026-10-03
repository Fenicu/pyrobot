import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { Outlook, ScenarioInfo } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import {
	READY,
	SCENARIO,
	VERDICT,
	WAKE,
	actDetail,
	candidateDetail,
	deedTag,
	lotteryTickets,
	scenarioText,
	timerLine,
	verdictText,
	verdictTone
} from './text';

const plan = fixture<Outlook>('outlook');
const openapi = JSON.parse(readFileSync(join(process.cwd(), '..', 'openapi.json'), 'utf-8'));

describe('словари плана', () => {
	it('причины таймеров — ровно enum из API', () => {
		const kinds: string[] = openapi.components.schemas.PlanTimerOut.properties.kind.enum;
		expect(Object.keys(WAKE).sort()).toEqual([...kinds].sort());
	});

	it('сценарии каталога — все с подписью, незнакомый — как есть', () => {
		const catalog = fixture<ScenarioInfo[]>('scenarios');
		expect(catalog.map((s) => s.name).filter((n) => !(n in SCENARIO))).toEqual([]);
		expect(scenarioText('deed:unknown')).toBe('deed:unknown');
	});

	it('вердикты планировщика — по-русски', () => {
		// Все вердикты, которые пишет app/engine/planner (reject, gate, doable_deeds, pick_personal).
		const verdicts = [
			'chosen', 'ok', 'busy', 'eating', 'no_motivation', 'no_money', 'no_details', 'no_value',
			'battle_window', 'sleep_deadline', 'factory_window', 'uncertified', 'cooldown', 'rate_limited',
			'not_feasible', 'no_hard_offer', 'cant_afford', 'sleep_not_allowed', 'market_closed', 'no_stock',
			'not_player', 'in_metro', 'metro_unknown_screen', 'reserved', 'no_team', 'company_unknown',
			'artifact_run', 'no_raw', 'motivation_cap', 'trip_pending'
		];
		expect(verdicts.filter((v) => !(v in VERDICT))).toEqual([]);
		expect(verdictText('stale:motivation')).toBe('нужно обновить: 🔥');
		expect(verdictText('stale:woke_at')).toBe('нужно обновить: woke_at');
		expect(verdictText('stale:trips')).toBe('нужно обновить: транспорт');
	});

	it('поездки: готовность вида, ожидание итога, обновление экрана транспорта', () => {
		const ready = { at: plan.now, kind: 'trip_ready' as const, key: 'tram', after_wake: false };
		expect(timerLine(ready, plan)).toEqual({ icon: '🚦', text: 'Можно ехать: 🚃 трамвай', detail: '' });
		const sled = { ...ready, key: 'sled' };
		expect(timerLine(sled, plan).text).toBe('Можно ехать: 🛷 санки');
		const unknown = { ...ready, key: '🚁Вертолёт' };
		expect(timerLine(unknown, plan).text).toBe('Можно ехать: 🚁Вертолёт');
		const refresh = { at: plan.now, kind: 'refresh' as const, key: 'trips', after_wake: false };
		expect(timerLine(refresh, plan).text).toBe('Можно снова обновить экран: транспорт');
		const result = { at: '2026-09-27T16:30:00Z', kind: 'trip_result' as const, key: null, after_wake: false };
		const pending = { scenario: 'trip', params: { vehicle: 'tram' }, score: null, verdict: 'trip_pending' };
		const withResult: Outlook = { ...plan, wakeups: [...plan.wakeups, result] };
		expect(candidateDetail(pending, withResult)).toMatch(/^до /);
		expect(actDetail('trip', { vehicle: 'bike' }, plan)).toBe('🚲 велосипед');
	});

	it('отказ из-за запаса 🔥 — под что он держится; у метро — только запас Горбушки', () => {
		expect(verdictText('reserved')).toBe('🔥 в запасе');
		expect(verdictTone('reserved')).toBe('muted');
		const deed = { scenario: 'deed:harvest', params: {}, score: 0.5, verdict: 'reserved' };
		expect(candidateDetail(deed, plan)).toBe('под метро');
		const both: Outlook = {
			...plan,
			reserves: [{ kind: 'gorbushka', motivation: 1, at: plan.now }, ...plan.reserves]
		};
		expect(candidateDetail(deed, both)).toBe('под Горбушку и метро');
		expect(candidateDetail({ ...deed, params: { today: 2 } }, both)).toBe('сегодня 2, под Горбушку и метро');
		const metro = { scenario: 'metro', params: {}, score: null, verdict: 'reserved' };
		expect(candidateDetail(metro, both)).toBe('под Горбушку');
	});

	it('причины неготовности цикла', () => {
		expect(Object.keys(READY).sort()).toEqual(
			['killed', 'lock_lost', 'paused', 'pipeline_unhealthy', 'spending_blocked', 'tg_offline'].sort()
		);
	});

	it('строки таймеров: сон с местом, лотерея из настроек, цель битвы', () => {
		const sleep = plan.wakeups.find((t) => t.kind === 'sleep_window')!;
		expect(timerLine(sleep, plan)).toEqual({ icon: '🛌', text: 'Сон 7 ч под мостом', detail: 'на текущих деньгах' });
		// Второй проход при устаревшей занятости — на последних известных деньгах, как и весь раздел.
		const stale = fixture<Outlook>('outlook_stale');
		const later = stale.wakeups.find((t) => t.kind === 'sleep_window')!;
		expect(timerLine(later, stale).detail).toBe('по последним данным о деньгах');
		const dump = plan.wakeups.find((t) => t.kind === 'stocks_dump')!;
		expect(timerLine(dump, plan).detail).toBe('цель 📯Pied Piper');
		expect(lotteryTickets(plan)).toBe('все — max');
		const custom = { ...plan, hints: { ...plan.hints, lottery_tickets: { money: 4, knowledge: 'max' as const } } };
		expect(lotteryTickets(custom)).toBe('💵 4, 📚 max');
		const cooldown = { at: plan.now, kind: 'cooldown' as const, key: 'refresh:profile', after_wake: false };
		expect(timerLine(cooldown, plan).text).toBe('Кончится отсрочка: обновить профиль');
	});

	it('метка дела для счётчиков: учёба и конфа делят значок 📚 в игре — метки различаются', () => {
		expect(deedTag('deed:learn')).not.toBe(deedTag('deed:confa'));
		expect(deedTag('deed:harvest')).toBe('⛏');
		expect(deedTag('deed:dconv')).toBe('⚙️→🔩');
	});
});
