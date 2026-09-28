import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { Outlook, ScenarioInfo } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import { READY, SCENARIO, VERDICT, WAKE, deedTag, lotteryTickets, scenarioText, timerLine, verdictText } from './text';

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
			'not_player', 'in_metro', 'metro_unknown_screen'
		];
		expect(verdicts.filter((v) => !(v in VERDICT))).toEqual([]);
		expect(verdictText('stale:motivation')).toBe('нужно обновить: 🔥');
		expect(verdictText('stale:woke_at')).toBe('нужно обновить: woke_at');
	});

	it('причины неготовности цикла', () => {
		expect(Object.keys(READY).sort()).toEqual(
			['killed', 'lock_lost', 'paused', 'pipeline_unhealthy', 'spending_blocked', 'tg_offline'].sort()
		);
	});

	it('строки таймеров: сон с местом, лотерея из настроек, цель битвы', () => {
		const sleep = plan.wakeups.find((t) => t.kind === 'sleep_window')!;
		expect(timerLine(sleep, plan)).toEqual({ icon: '🛌', text: 'Сон 7 ч под мостом', detail: 'на текущих деньгах' });
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
