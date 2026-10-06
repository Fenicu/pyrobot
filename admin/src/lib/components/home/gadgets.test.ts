import { cleanup, render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import type { EngineStatus, GadgetOut, GadgetTarget, GadgetsOut, Observed, GadgetsState, StateOut } from '$lib/api/types';
import { GadgetsStore } from '$lib/gadgets/store.svelte';
import { buyLine, lossOnFail, taskLine, targetLine } from '$lib/gadgets/text';
import { dialogs } from '$lib/stores/confirm.svelte';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import ConfirmDialog from '../ConfirmDialog.svelte';
import GadgetsCard from './GadgetsCard.svelte';

const prod = fixture<StateOut>('state');
const engine = fixture<EngineStatus>('engine_status');
const LIVE: EngineStatus = { ...engine, running: true, mode: 'live' };
const plain = (s: string | null) => s?.replace(/ /g, ' ') ?? null;

const PHONE: GadgetOut = {
	slot: '📱',
	up_slot: 'right',
	code: null,
	name: 'S-март',
	grade: '🔴',
	level: 18,
	bonuses: { practice: 39, theory: 20 },
	mark: null,
	set: 'summer',
	shop_tier: null
};
const HIJAB: GadgetOut = {
	slot: '🕶',
	up_slot: 'head',
	code: null,
	name: 'Хиджаб',
	grade: '⚫️',
	level: 26,
	bonuses: { theory: 85 },
	mark: '🧶',
	set: null,
	shop_tier: null
};
const IDLE_TASK: GadgetsOut['task'] = {
	status: 'idle',
	task_id: 0,
	slot: null,
	gadget: null,
	kind: null,
	target: null,
	start_level: null,
	end_level: null,
	started_at: null,
	ended_at: null,
	end_reason: null,
	level: null
};
const BASE: GadgetsOut = {
	now: '2026-10-07T09:00:00Z',
	worn: [PHONE, HIJAB],
	sets: ['⚫️Сет VIP'],
	bag: { used: 12, cap: 24 },
	upgrades: { white: 3, blue: 4, red: 33 },
	upgrade_info: { chances: { white: 50, blue: 70, red: 90 }, upgrademan_pct: null, confirm: true },
	buy: { enabled: false, plan: null, money: null },
	task: IDLE_TASK,
	progress: null
};
const ACTIVE: GadgetsOut = {
	...BASE,
	task: {
		...IDLE_TASK,
		status: 'active',
		task_id: 3,
		slot: 'right',
		gadget: 'S-март',
		kind: 'auto',
		target: 25,
		start_level: 12,
		started_at: '2026-10-07T08:00:00Z',
		level: 18
	},
	progress: { attempts: 37, ok: 29, fail: 8, spent: { white: 0, blue: 4, red: 33 }, level: 18 }
};
const target = (over: Partial<GadgetTarget>): GadgetTarget => ({
	set: 'autumn',
	status: 'saving',
	missing: [],
	in_bag: [],
	worn: [],
	blocked_by: [],
	need_money: 0,
	...over
});
const SAVING = target({
	missing: [
		{ slot: 'legs', tier: 12, price: 59_999 },
		{ slot: 'head', tier: 12, price: 59_999 },
		{ slot: 'chest', tier: 12, price: 65_999 },
		{ slot: 'torso', tier: 12, price: 65_999 }
	],
	worn: ['right', 'left'],
	need_money: 205_997
});
const MONEY = { cash: 1_120, stocks: 40_000, reserve: 1_000, available: 40_120 };

function snapshot(worn: GadgetOut[]): StateOut['state'] {
	const gadgets: Observed<GadgetsState> = {
		at: prod.now,
		src: 'screen',
		value: {
			items: worn.map(({ grade, level, slot, name, bonuses, mark }) => ({ grade, level, slot, name, bonuses, mark })),
			sets: ['⚫️Сет VIP']
		}
	};
	return { ...prod.state, gadgets };
}

function card(
	out: GadgetsOut | null,
	{ status = LIVE, reply = () => json(ACTIVE, 202) }: { status?: EngineStatus | null; reply?: (c: Call) => Response } = {}
) {
	const fetch = mockFetch(reply);
	const api = createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
	const onchange = vi.fn();
	render(ConfirmDialog);
	render(GadgetsCard, { api, state: snapshot(out?.worn ?? BASE.worn), stale: [], gadgets: out, error: null, status, onchange });
	return { fetch, onchange, region: screen.getByRole('region', { name: 'Гаджеты при тебе' }) };
}

describe('карточка «Гаджеты при тебе»', () => {
	beforeEach(() => dialogs.answer(null));

	it('«Точить» скрыта без движка и в dry_run, неактивна при задаче на другом слоте', () => {
		card(BASE, { status: null });
		expect(screen.queryByRole('button', { name: /^Точить/ })).toBeNull();
		cleanup();
		card(BASE, { status: { ...LIVE, mode: 'dry_run' } });
		expect(screen.queryByRole('button', { name: /^Точить/ })).toBeNull();
		cleanup();
		card(BASE, { status: { ...LIVE, running: false } });
		expect(screen.queryByRole('button', { name: /^Точить/ })).toBeNull();
		cleanup();

		const { region } = card(ACTIVE);
		const other = within(region).getByRole('button', { name: 'Точить: 🕶 Хиджаб' });
		expect(other).toBeDisabled();
		expect(other).toHaveAttribute('title', 'Идёт заточка 📱 S-март — сначала «Стоп»');
		expect(region).toHaveTextContent('Другой гаджет — после конца или «Стоп» текущей заточки');
		cleanup();

		const idle = card(BASE).region;
		expect(within(idle).getByRole('button', { name: 'Точить: 🕶 Хиджаб' })).toBeEnabled();
		expect(idle).toHaveTextContent('Рюкзак: 12/24');
		expect(idle).toHaveTextContent('⚫️Сет VIP');
	});

	it('окно «Точить»: цель по умолчанию 25, не ниже текущего+1, потеря ⌊N/4⌋, предупреждение VIP', async () => {
		const user = userEvent.setup();
		const { fetch, onchange } = card(BASE);
		await user.click(screen.getByRole('button', { name: 'Точить: 📱 S-март' }));
		let dialog = await screen.findByRole('dialog', { name: 'Точить: 📱 S-март' });
		expect(dialog).toHaveTextContent('Сейчас: 🔴18 ур.');
		const goal = within(dialog).getByRole('spinbutton', { name: 'Цель — уровень' });
		expect(goal).toHaveValue(25);
		expect(goal).toHaveAttribute('min', '19');
		expect(dialog).toHaveTextContent('Провал на ур. 18 теряет 4 ур.');
		expect(dialog).not.toHaveTextContent('⚫️Сет VIP');
		expect(within(dialog).getByRole('radio', { name: /🔴 · запас 33 · шанс 90%/ })).toBeInTheDocument();
		expect(within(dialog).getByRole('radio', { name: /^авто/ })).toBeChecked();
		await user.clear(goal);
		await user.type(goal, '18');
		expect(within(dialog).getByRole('button', { name: 'Точить' })).toBeDisabled();
		await user.clear(goal);
		await user.type(goal, '30');
		await user.click(within(dialog).getByRole('radio', { name: /^🔴/ }));
		await user.click(within(dialog).getByRole('button', { name: 'Точить' }));
		await vi.waitFor(() => expect(onchange).toHaveBeenCalledOnce());
		expect(fetch.calls.map((c) => [c.method, c.url, c.body])).toEqual([
			['POST', '/api/v1/accounts/1/gadgets/upgrade', JSON.stringify({ slot: 'right', target: 30, kind: 'red' })]
		]);

		await user.click(screen.getByRole('button', { name: 'Точить: 🕶 Хиджаб' }));
		dialog = await screen.findByRole('dialog', { name: 'Точить: 🕶 Хиджаб' });
		expect(within(dialog).getByRole('spinbutton')).toHaveValue(27);
		expect(dialog).toHaveTextContent('Провал на ур. 26 теряет 6 ур.');
		expect(dialog).toHaveTextContent('Провал снимет ⚫️Сет VIP');
		expect(lossOnFail(3)).toBe(0);
		expect(lossOnFail(40)).toBe(10);
	});

	it('строка задачи и «Стоп» с подтверждением', async () => {
		const user = userEvent.setup();
		const stopped: GadgetsOut = { ...ACTIVE, task: { ...ACTIVE.task, status: 'stopped', end_reason: 'stopped' } };
		const { fetch, region } = card(ACTIVE, { reply: () => json(stopped, 202) });
		const block = within(region).getByRole('group', { name: 'Заточка' });
		expect(block).toHaveTextContent('Точим 📱 S-март: 🔴18 → цель 25 (авто) · попыток 37 · ✓29 ✗8 · ⚪️0 🔵4 🔴33');
		expect(block).toHaveTextContent('запас ⚪️3 🔵4 🔴33 · шансы ⚪️50% 🔵70% 🔴90%');
		await user.click(within(block).getByRole('button', { name: 'Стоп' }));
		const ask = await screen.findByRole('dialog', { name: 'Остановить заточку?' });
		await user.click(within(ask).getByRole('button', { name: 'Не останавливать' }));
		expect(fetch.calls).toEqual([]);
		await user.click(within(block).getByRole('button', { name: 'Стоп' }));
		const again = await screen.findByRole('dialog', { name: 'Остановить заточку?' });
		await user.click(within(again).getByRole('button', { name: 'Остановить' }));
		await vi.waitFor(() => expect(fetch.calls).toHaveLength(1));
		expect(fetch.calls.map((c) => [c.method, c.url])).toEqual([['POST', '/api/v1/accounts/1/gadgets/upgrade/stop']]);
		expect(taskLine({ ...ACTIVE, progress: null })).toBe('Точим 📱 S-март: 🔴18 → цель 25 (авто)');
	});

	it('итог задачи виден до следующего старта', () => {
		const end = (status: GadgetsOut['task']['status'], end_level: number | null): GadgetsOut => ({
			...ACTIVE,
			task: { ...ACTIVE.task, status, end_level }
		});
		const done = card(end('done', 25)).region;
		expect(done).toHaveTextContent('Заточено: 📱 S-март — 25 ур., цель 25 достигнута · попыток 37');
		expect(within(done).queryByRole('button', { name: 'Стоп' })).toBeNull();
		cleanup();
		expect(card(end('exhausted', 20)).region).toHaveTextContent('Улучшения кончились: 📱 S-март — 20 ур. при цели 25');
		cleanup();
		expect(card(end('failed', null)).region).toHaveTextContent('Заточка прервана: 📱 S-март больше не надет');
		cleanup();
		const stopped = card(end('stopped', null)).region;
		expect(stopped).not.toHaveTextContent('Точим');
		expect(stopped).not.toHaveTextContent('Заточено');
	});

	it('блок покупки: копим, заблокирована, надет без сета, не подтверждена, рюкзак полон', () => {
		const pig = target({ set: 'pig', status: 'blocked', blocked_by: ['ring', 'book'] });
		const out: GadgetsOut = {
			...BASE,
			buy: {
				enabled: true,
				plan: {
					action: null,
					verdict: 'saving',
					target: SAVING,
					candidates: [
						pig,
						SAVING,
						target({ set: 'um', status: 'unconfirmed' }),
						target({ set: 'summer', status: 'worn_inactive' })
					]
				},
				money: MONEY
			}
		};
		expect(plain(buyLine(out))).toBe(
			'Копим на 🍂 Осень: куплено 2/6, на следующую часть не хватает $205 997, доступно $40 120 (наличные $1 120 + акции $40 000 − резерв $1 000)'
		);
		expect(targetLine(pig)).toBe('🐷 Свинтус: нужен 💍 или 💻 сета Свинтус или выше');
		const { region } = card(out);
		const block = within(region).getByRole('group', { name: 'Покупка гаджетов' });
		expect(block).toHaveTextContent('Копим на 🍂 Осень: куплено 2/6, на следующую часть не хватает $205 997');
		expect(block).toHaveTextContent('🐷 Свинтус: нужен 💍 или 💻 сета Свинтус или выше');
		expect(block).toHaveTextContent('Um-сет: надет, активация не подтверждена');
		expect(block).toHaveTextContent('🌞 Летний: надет, сет не появился');
		expect(block).not.toHaveTextContent('рюкзак полон');
		cleanup();

		const full: GadgetsOut = { ...out, bag: { used: 24, cap: 24 } };
		expect(within(card(full).region).getByRole('group', { name: 'Покупка гаджетов' })).toHaveTextContent(
			'рюкзак полон — покупка на паузе'
		);
		cleanup();

		const unknown: GadgetsOut = { ...BASE, buy: { enabled: true, plan: null, money: null } };
		const blind = within(card(unknown).region).getByRole('group', { name: 'Покупка гаджетов' });
		expect(blind).toHaveTextContent('резерв неизвестен');
		expect(blind).not.toHaveTextContent('$');
		cleanup();

		expect(within(card(BASE).region).queryByRole('group', { name: 'Покупка гаджетов' })).toBeNull();
	});

	it('409 карточки — свои тексты', async () => {
		const user = userEvent.setup();
		const { region } = card(BASE, { reply: () => json({ detail: 'dry_run' }, 409) });
		await user.click(screen.getByRole('button', { name: 'Точить: 📱 S-март' }));
		const dialog = await screen.findByRole('dialog');
		await user.click(within(dialog).getByRole('button', { name: 'Точить' }));
		expect(await within(region).findByRole('alert')).toHaveTextContent('Заточка — только в режиме live');
		cleanup();

		const active = card(ACTIVE, { reply: () => json({ detail: 'no_task' }, 409) });
		await user.click(within(active.region).getByRole('button', { name: 'Стоп' }));
		const ask = await screen.findByRole('dialog', { name: 'Остановить заточку?' });
		await user.click(within(ask).getByRole('button', { name: 'Остановить' }));
		expect(await within(active.region).findByRole('alert')).toHaveTextContent('Заточка не идёт — нечего останавливать');
	});
});

describe('стор гаджетов', () => {
	it('перечитывает на кадр settings и на state с полями карточки', async () => {
		const fetch = mockFetch(() => json(BASE));
		const api = createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
		const store = new GadgetsStore(api);
		store.start();
		await vi.waitFor(() => expect(store.data).not.toBeNull());
		store.onEvent({ type: 'settings', id: '1', data: { version: 2, mode: 'live', paused: false, killed: false } });
		await vi.waitFor(() => expect(fetch.calls.length).toBe(2));
		store.onEvent({ type: 'state', id: '2', data: { version: 3, changed: { motivation: null } } });
		store.onEvent({ type: 'state', id: '3', data: { version: 4, changed: { upgrades: null } } });
		await vi.waitFor(() => expect(fetch.calls.length).toBe(3));
		expect(fetch.calls.every((c) => c.url === '/api/v1/accounts/1/gadgets')).toBe(true);
		store.stop();
	});
});
