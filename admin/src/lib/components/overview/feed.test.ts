import { cleanup, render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { FeedItem, OverviewFeed } from '$lib/api/types';
import type { FeedFilters } from '$lib/overview/feed';
import { fixture } from '$lib/test/fixtures';
import { accountOut } from '$lib/test/overview';
import FeedFiltersView from './FeedFilters.svelte';
import FeedList from './FeedList.svelte';

const feed = fixture<OverviewFeed>('overview_feed');
const accounts = [accountOut(1, 'Альфа'), accountOut(2, 'Бета', { status: 'error' }), accountOut(3, 'Гамма')];
const NOW = new Date('2026-10-17T09:00:00Z');

afterEach(cleanup);

describe('список событий', () => {
	it('строка на событие: время МСК, аккаунт-ссылка, значок и текст; порядок как пришёл', () => {
		render(FeedList, { items: feed.items, accounts, now: NOW });
		const rows = screen.getAllByRole('listitem');
		expect(rows).toHaveLength(feed.items.length);
		const first = within(rows[0]!);
		expect(first.getByText('11:58')).toBeInTheDocument();
		expect(first.getByRole('link', { name: 'Бета' })).toHaveAttribute('href', '/a/2');
		expect(first.getByText(/scenario deed:learn failed/)).toBeInTheDocument();
		expect(first.getByText('⛔')).toBeInTheDocument();
		expect(within(rows[1]!).getByText(/⚔ битва · \+\$1\s400, \+800 💡/)).toBeInTheDocument();
	});

	it('запись другого дня подписана датой', () => {
		render(FeedList, { items: feed.items, accounts, now: NOW });
		const last = screen.getAllByRole('listitem').at(-1)!;
		expect(last.textContent).toContain('16.10 22:00');
	});

	it('точка статуса аккаунта и «#id» для неизвестного', () => {
		const lone: FeedItem = { ...feed.items[1]!, account_id: 77 };
		render(FeedList, { items: [feed.items[0]!, lone], accounts, now: NOW });
		const rows = screen.getAllByRole('listitem');
		expect(within(rows[0]!).getByRole('img', { name: 'ошибка' })).toBeInTheDocument();
		expect(within(rows[1]!).getByRole('link', { name: '#77' })).toHaveAttribute('href', '/a/77');
	});

	it('незнакомый вид журнала печатается как есть', () => {
		const odd: FeedItem = { ...feed.items[1]!, key: 'l:1', kind: 'zzz_new', amounts: { money: 5 } };
		render(FeedList, { items: [odd], accounts, now: NOW });
		expect(screen.getByText('zzz_new · +$5')).toBeInTheDocument();
	});

	it('пусто - сообщение', () => {
		render(FeedList, { items: [], accounts, now: NOW });
		expect(screen.getByText('Событий пока нет.')).toBeInTheDocument();
	});
});

describe('фильтры ленты', () => {
	const none: FeedFilters = { accountIds: [], types: [] };

	function show(value: FeedFilters = none) {
		const onchange = vi.fn();
		render(FeedFiltersView, { accounts, value, onchange });
		return onchange;
	}

	it('типы - переключатели; по умолчанию нажата «Все»', async () => {
		const user = userEvent.setup();
		const onchange = show();
		const group = screen.getByRole('group', { name: 'Типы событий' });
		expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual([
			'Все',
			'Уровень',
			'Битва',
			'Фабрика',
			'Метро',
			'Лотерея',
			'Предупреждения',
			'Вмешательства'
		]);
		expect(within(group).getByRole('button', { name: 'Все' })).toHaveAttribute('aria-pressed', 'true');
		await user.click(within(group).getByRole('button', { name: 'Битва' }));
		expect(onchange).toHaveBeenCalledWith({ accountIds: [], types: ['battle'] });
	});

	it('нажатый тип снимается; «Все» сбрасывает выбор', async () => {
		const user = userEvent.setup();
		const onchange = show({ accountIds: [], types: ['battle', 'alert'] });
		const group = screen.getByRole('group', { name: 'Типы событий' });
		expect(within(group).getByRole('button', { name: 'Битва' })).toHaveAttribute('aria-pressed', 'true');
		expect(within(group).getByRole('button', { name: 'Все' })).toHaveAttribute('aria-pressed', 'false');
		await user.click(within(group).getByRole('button', { name: 'Битва' }));
		expect(onchange).toHaveBeenLastCalledWith({ accountIds: [], types: ['alert'] });
		await user.click(within(group).getByRole('button', { name: 'Все' }));
		expect(onchange).toHaveBeenLastCalledWith({ accountIds: [], types: [] });
	});

	it('аккаунты - мультивыбор с флажками; подпись показывает выбор', async () => {
		const user = userEvent.setup();
		const onchange = show({ accountIds: [2], types: [] });
		expect(screen.getByText('Аккаунты: 1 из 3')).toBeInTheDocument();
		await user.click(screen.getByText('Аккаунты: 1 из 3'));
		const box = screen.getByRole('checkbox', { name: 'Бета' });
		expect(box).toBeChecked();
		await user.click(screen.getByRole('checkbox', { name: 'Альфа' }));
		expect(onchange).toHaveBeenLastCalledWith({ accountIds: [2, 1], types: [] });
		await user.click(box);
		expect(onchange).toHaveBeenLastCalledWith({ accountIds: [], types: [] });
		await user.click(screen.getByRole('button', { name: 'Все аккаунты' }));
		expect(onchange).toHaveBeenLastCalledWith({ accountIds: [], types: [] });
	});

	it('без выбора аккаунтов - «Аккаунты: все»', () => {
		show();
		expect(screen.getByText('Аккаунты: все')).toBeInTheDocument();
	});
});
