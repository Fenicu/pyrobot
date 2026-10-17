import { cleanup, render, screen, within } from '@testing-library/svelte';
import { afterEach, describe, expect, it } from 'vitest';
import type { BattleHq } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import BattleSummary from './BattleSummary.svelte';

const hq = fixture<BattleHq>('overview_battle');
const first = hq.battles[0]!;
const second = hq.battles[1]!;

afterEach(cleanup);

describe('сводка по целям', () => {
	it('плитка на цель: счёт, уровни, навыки, выставлено', () => {
		render(BattleSummary, { battle: first, nearest: true });
		const umbrella = screen.getByRole('group', { name: '☂️Umbrella' });
		expect(umbrella).toHaveTextContent('2 акк.');
		expect(umbrella).toHaveTextContent('🎚 125 · ср. 62,5');
		expect(umbrella).toHaveTextContent('🔨125 🎓106 🐿62 🐢48');
		expect(umbrella).toHaveTextContent('выставлено 1 из 2');
		const hooli = screen.getByRole('group', { name: '🤖Hooli' });
		// Уровень неизвестен: аккаунт в счёте, но не в суммах.
		expect(hooli).toHaveTextContent('уровень неизвестен: 1');
	});

	it('аккаунты цели с отметкой «выставлена в игре»', () => {
		render(BattleSummary, { battle: first, nearest: true });
		const umbrella = screen.getByRole('group', { name: '☂️Umbrella' });
		const rows = within(umbrella).getAllByRole('listitem');
		expect(rows.map((r) => r.textContent?.replace(/\s+/g, ' ').trim())).toEqual([
			'☣️Iko 54 ✓ выставлена',
			'☣️Fenicu 71 ✗ не выставлена'
		]);
		const defense = screen.getByRole('group', { name: '🛡Защита' });
		expect(within(defense).getByRole('listitem')).toHaveTextContent('своя компания: защита');
		expect(within(defense).getByRole('listitem')).toHaveTextContent('— неизвестно');
	});

	it('следующая битва: готовность не показывается', () => {
		render(BattleSummary, { battle: second, nearest: false });
		expect(screen.queryByText(/выставлено/)).toBeNull();
		expect(screen.getAllByText(/неизвестно/).length).toBeGreaterThan(0);
	});

	it('причина, по которой приказ не дойдёт, рядом с аккаунтом', () => {
		render(BattleSummary, { battle: first, nearest: true });
		const hooli = screen.getByRole('group', { name: '🤖Hooli' });
		expect(hooli).toHaveTextContent('на паузе');
	});

	it('нет аккаунтов — ничего не рисуется', () => {
		const { container } = render(BattleSummary, { battle: { ...first, accounts: [], summary: [] }, nearest: true });
		expect(container.querySelector('[role="group"]')).toBeNull();
	});
});
