import { cleanup, render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AccountOut, SearchAccountOut, SearchHitOut, SearchOut } from '$lib/api/types';
import { accountOut } from '$lib/test/overview';
import SearchAccounts from './SearchAccounts.svelte';
import SearchForm from './SearchForm.svelte';
import SearchHitItem from './SearchHitItem.svelte';
import SearchResults from './SearchResults.svelte';

const ACCOUNTS: AccountOut[] = [accountOut(1, 'Альфа'), accountOut(2, 'Бета'), accountOut(3, 'Гамма')];

const accountsStatus: SearchAccountOut[] = [
	{ account_id: 1, name: 'Альфа', status: 'ok', reason: null, count: 2 },
	{ account_id: 2, name: 'Бета', status: 'unavailable', reason: 'engine_not_running', count: 0 },
	{ account_id: 3, name: 'Гамма', status: 'error', reason: 'timeout', count: 0 }
];

const sampleHits: SearchHitOut[] = [
	{
		account_id: 1,
		chat_id: 100,
		msg_id: 10,
		date: '2026-10-17T12:00:00Z',
		outgoing: false,
		text: 'Ты надел маску 1\nВторая строка',
		markup: {
			inline: [['Кнопка 1', 0, 0, 'btn1', null, null]],
			reply: [['Ответ 1', 'Ответ 2']]
		}
	},
	{
		account_id: 1,
		chat_id: 100,
		msg_id: 11,
		date: '2026-10-17T12:05:00Z',
		outgoing: true,
		text: 'Ты надел маску 2\nДругая вторая строка',
		markup: null
	},
	{
		account_id: 2,
		chat_id: 200,
		msg_id: 20,
		date: '2026-10-17T11:00:00Z',
		outgoing: false,
		text: 'Другое событие игры',
		markup: null
	}
];

afterEach(cleanup);

describe('SearchAccounts status chips', () => {
	it('показывает фишки статусов аккаунтов с количеством и причинами на русском', () => {
		render(SearchAccounts, { accounts: accountsStatus });
		expect(screen.getByText('Альфа: 2')).toBeInTheDocument();
		expect(screen.getByText('Бета: движок не запущен')).toBeInTheDocument();
		expect(screen.getByText('Гамма: таймаут ожидания')).toBeInTheDocument();
	});
});

describe('SearchHitItem', () => {
	const NOW = new Date('2026-10-17T13:00:00Z');

	it('отображает аккаунт, время МСК, стрелку направления, текст и кнопки-пилюли', () => {
		render(SearchHitItem, {
			hit: sampleHits[0]!,
			accountName: 'Альфа',
			now: NOW
		});

		expect(screen.getByText('Альфа')).toBeInTheDocument();
		expect(screen.getByText('← игра')).toBeInTheDocument();
		expect(screen.getByText(/Ты надел маску 1/)).toBeInTheDocument();
		expect(screen.getByText('Кнопка 1')).toBeInTheDocument();
		expect(screen.getByText('Ответ 1')).toBeInTheDocument();
		expect(screen.getByText('Ответ 2')).toBeInTheDocument();
	});

	it('для исходящего показывает «→ игре»', () => {
		render(SearchHitItem, {
			hit: sampleHits[1]!,
			accountName: 'Альфа',
			now: NOW
		});
		expect(screen.getByText('→ игре')).toBeInTheDocument();
	});

	it('копирует текст сообщения в буфер обмена по клику', async () => {
		const user = userEvent.setup();
		const writeText = vi.fn().mockResolvedValue(undefined);
		vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });

		render(SearchHitItem, {
			hit: sampleHits[0]!,
			accountName: 'Альфа',
			now: NOW
		});

		const copyBtn = screen.getByRole('button', { name: /копировать/i });
		await user.click(copyBtn);
		expect(writeText).toHaveBeenCalledWith('Ты надел маску 1\nВторая строка');
	});
});

describe('SearchResults', () => {
	const NOW = new Date('2026-10-17T13:00:00Z');

	it('по умолчанию группирует совпадения по нормализованной первой строке', () => {
		render(SearchResults, {
			hits: sampleHits,
			accountsMap: new Map([[1, 'Альфа'], [2, 'Бета']]),
			now: NOW
		});

		// 2 совпадения в группе «Ты надел маску N», 1 в группе «Другое событие игры»
		expect(screen.getByText('Ты надел маску 1')).toBeInTheDocument();
		expect(screen.getAllByText('Другое событие игры').length).toBeGreaterThanOrEqual(1);
		expect(screen.getByText('2')).toBeInTheDocument();
	});

	it('переключатель «Списком» разворачивает плоский список по дате desc', async () => {
		const user = userEvent.setup();
		render(SearchResults, {
			hits: sampleHits,
			accountsMap: new Map([[1, 'Альфа'], [2, 'Бета']]),
			now: NOW
		});

		const toggleBtn = screen.getByRole('button', { name: /списком/i });
		await user.click(toggleBtn);

		// В плоском списке все сообщения отображаются сразу
		const items = screen.getAllByTestId('search-hit');
		expect(items).toHaveLength(3);
	});

	it('показывает сообщение, если ничего не найдено', () => {
		render(SearchResults, {
			hits: [],
			accountsMap: new Map(),
			now: NOW
		});
		expect(screen.getByText('Ничего не найдено.')).toBeInTheDocument();
	});
});

describe('SearchForm', () => {
	it('позволяет вводить текст, переключать где искать и отправлять форму', async () => {
		const user = userEvent.setup();
		const onsubmit = vi.fn();

		render(SearchForm, {
			q: 'маска',
			where: 'db',
			accountIds: [],
			limit: 50,
			accounts: ACCOUNTS,
			loading: false,
			validationError: null,
			onsubmit
		});

		const input = screen.getByPlaceholderText('Текст из игры, например: надел маску');
		expect(input).toHaveValue('маска');

		const tgBtn = screen.getByRole('button', { name: /Telegram/i });
		await user.click(tgBtn);

		const submitBtn = screen.getByRole('button', { name: 'Искать' });
		await user.click(submitBtn);

		expect(onsubmit).toHaveBeenCalledWith(expect.objectContaining({
			q: 'маска',
			where: 'telegram'
		}));
	});

	it('показывает подсказку ожидания при поиске в Telegram', () => {
		render(SearchForm, {
			q: 'маска',
			where: 'telegram',
			accountIds: [],
			limit: 50,
			accounts: ACCOUNTS,
			loading: true,
			validationError: null,
			onsubmit: vi.fn()
		});

		expect(screen.getByText('Ищу в Telegram… это может занять до 15 секунд на аккаунт')).toBeInTheDocument();
	});
});
