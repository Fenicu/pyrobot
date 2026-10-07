import type { MetroLive } from '$lib/api/types';

/** Кадр `metro_live` по форме `live_frame` (`app/engine/metro/live.py`): забег идёт 10 минут из
 * 40 бюджета, бот на втором шаге обхода, в кармане 💵 30. */
export function liveFrame(over: Partial<MetroLive> = {}): MetroLive {
	return {
		message_id: 500,
		scenario_run_id: 7,
		running: true,
		started_at: '2026-10-07T18:00:00+00:00',
		battle_at: '2026-10-07T19:00:00+00:00',
		kick_at: '2026-10-07T18:45:00+00:00',
		budget: { total_s: 2400, used: 0.25, step_s: 5.2 },
		grid: {
			cells: { '0,0': '.', '0,1': '.', '0,2': '.', '1,0': '#', '1,1': '.', '1,2': '#' },
			visited: [
				[0, 0],
				[0, 1],
				[1, 1]
			]
		},
		pos: [1, 1],
		exit: null,
		path: [
			[0, 0],
			[0, 1],
			[1, 1]
		],
		vitals: [
			{ step: 0, pos: [0, 0], stamina: 121, packs: 3 },
			{ step: 2, pos: [1, 1], stamina: 118, packs: 3 }
		],
		events: [
			{ step: 1, pos: [0, 1], kind: 'metro_loot', item: 'money', amount: 30 },
			{ step: 2, pos: [1, 1], kind: 'metro_npc', strength: 'low' }
		],
		steps: 2,
		mode: 'explore',
		leave_reason: null,
		stamina: 118,
		packs: 3,
		found: { money: 30 },
		last_event: { step: 2, pos: [1, 1], kind: 'metro_npc', strength: 'low' },
		outcome: null,
		...over
	};
}
