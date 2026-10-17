import { cleanup, render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { BattleHq, HqBattle } from '$lib/api/types';
import type { SaveResult } from '$lib/overview/battle.svelte';
import { fixture } from '$lib/test/fixtures';
import BattleCard from './BattleCard.svelte';

const hq = fixture<BattleHq>('overview_battle');
const first = hq.battles[0]!;
const second = hq.battles[1]!;

function renderCard(over: Partial<{ battle: HqBattle; nearest: boolean; saving: boolean; result: SaveResult | null }> = {}) {
	const onsave = vi.fn();
	const props = {
		battle: first,
		nearest: true,
		targets: hq.targets,
		companies: hq.companies,
		saving: false,
		result: null,
		onsave,
		...over
	};
	const view = render(BattleCard, props);
	return { onsave, props, ...view };
}

afterEach(cleanup);

describe('карточка приказов', () => {
	it('заголовок с отсчётом, форма заполнена текущими приказами', () => {
		renderCard();
		const card = screen.getByRole('region', { name: 'Ближайшая битва 13:00 (через 42 мин)' });
		expect(within(card).getByLabelText('Всем')).toHaveValue('🤖Hooli');
		expect(within(card).getByLabelText('☣️Black Mesa')).toHaveValue('☂️Umbrella');
		expect(within(card).getByLabelText('🤖Hooli')).toHaveValue('');
		expect(within(card).getByLabelText('📯Pied Piper')).toHaveValue('');
	});

	it('строка корпорации не предлагает атаку на себя, но предлагает защиту; «всем» — всё', () => {
		renderCard();
		const own = screen.getByLabelText('🤖Hooli');
		const ownOptions = within(own).getAllByRole('option').map((o) => o.textContent);
		expect(ownOptions).toEqual(['Не задавать', '📯Pied Piper', '⚡️Stark Ind.', '☂️Umbrella', '🎩Wayne Ent.', '☣️Black Mesa', '🛡Защита']);
		const all = within(screen.getByLabelText('Всем')).getAllByRole('option');
		expect(all).toHaveLength(hq.targets.length + 1);
		expect(all[0]).toHaveTextContent('Не задавать');
	});

	it('«Сохранить» шлёт точное тело; пока форма не тронута, кнопка выключена', async () => {
		const user = userEvent.setup();
		const { onsave } = renderCard();
		const save = screen.getByRole('button', { name: 'Сохранить' });
		expect(save).toBeDisabled();
		await user.selectOptions(screen.getByLabelText('Всем'), '☂️Umbrella');
		await user.selectOptions(screen.getByLabelText('📯Pied Piper'), '🛡Защита');
		await user.selectOptions(screen.getByLabelText('☣️Black Mesa'), '');
		expect(save).toBeEnabled();
		await user.click(save);
		expect(onsave).toHaveBeenCalledExactlyOnceWith(first.battle_at, {
			all: '☂️Umbrella',
			corporations: { piper: '🛡Защита' }
		});
	});

	it('«Отменить приказы» шлёт пустое тело и сразу очищает форму', async () => {
		const user = userEvent.setup();
		const { onsave } = renderCard();
		await user.click(screen.getByRole('button', { name: 'Отменить приказы' }));
		expect(onsave).toHaveBeenCalledExactlyOnceWith(first.battle_at, { all: null, corporations: {} });
		expect(screen.getByLabelText('Всем')).toHaveValue('');
		expect(screen.getByLabelText('☣️Black Mesa')).toHaveValue('');
	});

	it('без приказов отменять нечего', () => {
		renderCard({ battle: second, nearest: false });
		expect(screen.getByRole('region', { name: 'Следующая 22:00' })).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Отменить приказы' })).toBeDisabled();
	});

	it('за минуту до битвы форма закрыта, предупреждение сказано', () => {
		const late: HqBattle = { ...first, editable: false, note: 'too_late' };
		renderCard({ battle: late });
		expect(screen.getByLabelText('Всем')).toBeDisabled();
		expect(screen.getByLabelText('☣️Black Mesa')).toBeDisabled();
		expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
		expect(screen.getByRole('button', { name: 'Отменить приказы' })).toBeDisabled();
		expect(screen.getByText('до битвы меньше минуты: приказ уже не применится')).toBeInTheDocument();
	});

	it('предупреждения: защита и не поставят цель', () => {
		renderCard();
		const list = screen.getByRole('list', { name: 'Предупреждения' });
		expect(within(list).getAllByRole('listitem').map((li) => li.textContent?.trim())).toEqual([
			'1 аккаунт пойдёт в 🛡Защиту: цель — его компания',
			'1 аккаунт цель не поставит: на паузе'
		]);
	});

	it('пока идёт запись, форма заблокирована', () => {
		renderCard({ saving: true });
		expect(screen.getByLabelText('Всем')).toBeDisabled();
		expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
	});

	it('правка не затирается опросом, а сохранённое подхватывается', async () => {
		const user = userEvent.setup();
		const { rerender, props } = renderCard();
		await user.selectOptions(screen.getByLabelText('Всем'), '🎩Wayne Ent.');
		// Пришли те же приказы (опрос): набранное остаётся.
		await rerender({ ...props, battle: { ...first } });
		expect(screen.getByLabelText('Всем')).toHaveValue('🎩Wayne Ent.');
		// Приказ сохранён: форма равна серверу, кнопка снова выключена.
		const next = { ...first, orders: { all: '🎩Wayne Ent.', corporations: first.orders.corporations } };
		await rerender({ ...props, battle: next });
		expect(screen.getByLabelText('Всем')).toHaveValue('🎩Wayne Ent.');
		expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
	});

	it('нетронутая форма принимает приказы, изменённые на сервере', async () => {
		const { rerender, props } = renderCard();
		const next = { ...first, orders: { all: null, corporations: { piper: '🤖Hooli' } } };
		await rerender({ ...props, battle: next });
		expect(screen.getByLabelText('Всем')).toHaveValue('');
		expect(screen.getByLabelText('📯Pied Piper')).toHaveValue('🤖Hooli');
		expect(screen.getByLabelText('☣️Black Mesa')).toHaveValue('');
	});

	it('итог: ошибка текстом, успех — кого разбудили', () => {
		const error: SaveResult = {
			battleAt: first.battle_at,
			error: {
				kind: 'validation',
				status: 422,
				issues: [{ loc: ['body', 'corporations', 'hooli'], msg: 'x', type: 'target_is_own_company' }]
			},
			woken: [],
			notWoken: []
		};
		const view = renderCard({ result: error });
		expect(screen.getByRole('alert')).toHaveTextContent('Корпорация «🤖Hooli» не может атаковать саму себя');
		view.unmount();
		const ok: SaveResult = {
			battleAt: first.battle_at,
			error: null,
			woken: [1, 2],
			notWoken: [{ account_id: 3, reason: 'locked_elsewhere' }]
		};
		renderCard({ result: ok });
		const status = screen.getByRole('status');
		expect(status).toHaveTextContent('Сохранено. Движки разбужены: 2');
		expect(status).toHaveTextContent('Не разбужены: 🤖Khalida (на другом хосте)');
	});

	it('итог чужой битвы не показывается', () => {
		renderCard({ result: { battleAt: second.battle_at, error: null, woken: [1], notWoken: [] } });
		expect(screen.queryByRole('status')).toBeNull();
	});
});
