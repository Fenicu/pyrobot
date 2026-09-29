import { describe, expect, it } from 'vitest';
import { legend, markers } from './events';

describe('метки событий на графиках', () => {
	it('значок и подпись — как в плане, x — секунды', () => {
		const marks = markers([
			{ at: '2026-09-27T18:45:06Z', scenario: 'stocks_dump' },
			{ at: '2026-09-27T18:56:11Z', scenario: 'stocks_dump' },
			{ at: '2026-09-27T19:05:12Z', scenario: 'sleep' },
			{ at: 'битое', scenario: 'sleep' }
		]);
		expect(marks).toEqual([
			{ x: Date.UTC(2026, 8, 27, 18, 45, 6) / 1000, icon: '📈', label: 'слив налички в акции' },
			{ x: Date.UTC(2026, 8, 27, 18, 56, 11) / 1000, icon: '📈', label: 'слив налички в акции' },
			{ x: Date.UTC(2026, 8, 27, 19, 5, 12) / 1000, icon: '🛌', label: 'сон' }
		]);
		expect(legend(marks)).toBe('📈 слив налички в акции · 🛌 сон');
	});

	it('незнакомый сценарий — точкой и кодом', () => {
		expect(markers([{ at: '2026-09-27T18:00:00Z', scenario: 'new_thing' }])[0]).toMatchObject({ icon: '•', label: 'new_thing' });
	});
});
