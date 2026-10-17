import { cleanup, render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it } from 'vitest';
import type { ScheduleOut } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import ScheduleTimeline from './ScheduleTimeline.svelte';

const sched = fixture<ScheduleOut>('overview_schedule');

afterEach(cleanup);

describe('компонент ScheduleTimeline', () => {
	it('отрисовывает строку для каждого аккаунта и отрезки занятости', () => {
		const names = new Map([
			[1, 'Первый бот'],
			[2, 'Второй бот']
		]);
		render(ScheduleTimeline, { schedule: sched, accountNames: names });

		expect(screen.getByText('Первый бот')).toBeInTheDocument();
		expect(screen.getByText('Второй бот')).toBeInTheDocument();

		// Проверка отрезка занятости
		expect(screen.getByText(/добыча/)).toBeInTheDocument();
		// Проверка сна
		expect(screen.getByText(/Сон \(под мостом\)/)).toBeInTheDocument();

		// У второго аккаунта stale: true -> подпись снимка
		expect(screen.getByText(/снимок от/)).toBeInTheDocument();

		// Засечки времени в шапке
		expect(screen.getByText('12:00')).toBeInTheDocument();
	});

	it('пустое состояние: при пустом списке аккаунтов выводит ссылку на /accounts', () => {
		const empty: ScheduleOut = { ...sched, accounts: [] };
		render(ScheduleTimeline, { schedule: empty });

		const region = screen.getByRole('region', { name: 'Нет аккаунтов' });
		expect(region).toBeInTheDocument();
		const link = screen.getByRole('link', { name: 'Открыть аккаунты' });
		expect(link).toHaveAttribute('href', '/accounts');
	});
});
