import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Outlook, StateOut } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import PlanCard from './PlanCard.svelte';

const plan = fixture<Outlook>('outlook');
const prod = fixture<StateOut>('state');
const NOW = new Date(plan.now);

function card(p: Outlook = plan) {
	render(PlanCard, { plan: p, error: null, state: prod.state, now: NOW });
	return screen.getByRole('region', { name: 'План бота' });
}

describe('«План бота» на фикстуре из бэкенд-теста', () => {
	it('«Сейчас», «Почему не другое», «Готово сейчас», «Дальше по времени»', () => {
		const block = card();
		const now = within(block).getByRole('region', { name: 'Сейчас' });
		expect(now).toHaveTextContent('🤑 билеты лотереи (все — max)');
		expect(now).toHaveTextContent('Занят: работа до 19:50');
		expect(now).toHaveTextContent('сегодня ⛏ 3, ⚙️→🔩 2 — следующей будет переработка');

		const why = within(block).getByRole('region', { name: 'Почему не другое' });
		const rows = within(why).getAllByRole('listitem');
		expect(rows.map((r) => r.dataset.verdict)).toEqual(['busy', 'chosen']);
		expect(rows[0]).toHaveTextContent('🔨 прокачка навыков');
		expect(rows[0]).toHaveTextContent('занят · до 19:50');
		expect(rows[1]).toHaveTextContent('выбрано');

		const ready = within(block).getByRole('region', { name: /Готово сейчас/ });
		expect(ready).toHaveTextContent('на текущем снимке');
		expect(ready).toHaveTextContent('🍊 мандарин');

		const next = within(block).getByRole('region', { name: 'Дальше по времени' });
		const timers = within(next).getAllByRole('listitem');
		expect(timers).toHaveLength(8);
		expect(timers[0]).toHaveTextContent('19:50');
		expect(timers[0]).toHaveTextContent('Освободится');
		expect(timers[0]).toHaveTextContent('через 20 мин');
		expect(timers[3]).toHaveTextContent('22:05');
		expect(timers[3]).toHaveTextContent('Сон 7 ч под мостом');
		expect(timers[7]).toHaveTextContent('Слив налички в акции перед битвой');
		expect(timers[7]).toHaveTextContent('цель 📯Pied Piper');
	});

	it('при паузе решение — условное', () => {
		card({ ...plan, loop: { ...plan.loop, paused: true, ready: 'paused' } });
		const now = screen.getByRole('region', { name: 'Сейчас' });
		expect(now).toHaveTextContent('⏸ Планировщик на паузе');
		expect(now).toHaveTextContent('когда пауза снимется — 🤑 билеты лотереи (все — max)');
	});

	it('во сне таймеры прохода «после пробуждения» — под подзаголовком', () => {
		const until = plan.wakeups[0]!.at;
		const asleep: Outlook = {
			...plan,
			phase: 'asleep',
			busy: { activity: 'sleep_hotel', until },
			decision: { kind: 'wait', scenario: null, params: {}, reason: 'busy', until },
			considered: [],
			also_ready: [],
			wakeups: plan.wakeups.map((t) => ({ ...t, after_wake: t.kind !== 'busy' }))
		};
		card(asleep);
		const next = screen.getByRole('region', { name: 'Дальше по времени' });
		expect(next).toHaveTextContent('Проснётся');
		expect(within(next).getByText('после пробуждения')).toBeInTheDocument();
		expect(screen.getByRole('region', { name: 'Сейчас' })).toHaveTextContent('🛌 ждёт пробуждения — следующий шаг в 19:50');
	});

	it('на телефоне — «Сейчас» и первые 5 событий, остальное под «Ещё»', async () => {
		const user = userEvent.setup();
		card();
		const timers = within(screen.getByRole('region', { name: 'Дальше по времени' })).getAllByRole('listitem');
		// Раскладку решает CSS: на телефоне скрыто классом hidden, на ПК (md) видно.
		expect(timers.slice(0, 5).every((t) => !t.classList.contains('hidden'))).toBe(true);
		expect(timers.slice(5).every((t) => t.classList.contains('hidden') && t.classList.contains('md:grid'))).toBe(true);
		const why = screen.getByRole('region', { name: 'Почему не другое' });
		expect(why).toHaveClass('hidden', 'md:block');
		const more = screen.getByRole('button', { name: 'Ещё' });
		expect(more).toHaveAttribute('aria-expanded', 'false');
		expect(more).toHaveClass('md:hidden');
		await user.click(more);
		expect(timers.every((t) => !t.classList.contains('hidden'))).toBe(true);
		expect(why).not.toHaveClass('hidden');
		expect(screen.getByRole('button', { name: 'Свернуть' })).toHaveAttribute('aria-expanded', 'true');
	});

	it('ошибка без плана и загрузка', () => {
		render(PlanCard, { plan: null, error: { kind: 'engine_down', status: 503, code: 'planner not started' }, state: {}, now: NOW });
		expect(screen.getByRole('alert')).toHaveTextContent('План недоступен: Движок недоступен.');
	});
});
