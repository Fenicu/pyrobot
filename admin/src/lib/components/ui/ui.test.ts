import Bell from '@lucide/svelte/icons/bell';
import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { createRawSnippet } from 'svelte';
import { describe, expect, it, vi } from 'vitest';
import Card from './Card.svelte';
import PageHeader from './PageHeader.svelte';
import Row from './Row.svelte';
import StatusDot from './StatusDot.svelte';
import Tile from './Tile.svelte';
import Toggle from './Toggle.svelte';

const html = (markup: string) => createRawSnippet(() => ({ render: () => markup }));

describe('PageHeader', () => {
	it('заголовок с подзаголовком, статус и действия', () => {
		render(PageHeader, {
			title: 'Iko',
			subtitle: 'Настройки',
			status: html('<span>LIVE</span>'),
			actions: html('<button>История</button>')
		});
		const h1 = screen.getByRole('heading', { level: 1 });
		expect(h1).toHaveTextContent('Iko · Настройки');
		expect(screen.getByRole('banner')).toContainElement(screen.getByText('LIVE'));
		expect(screen.getByRole('button', { name: 'История' })).toBeInTheDocument();
	});

	it('без подзаголовка — только название', () => {
		render(PageHeader, { title: 'Аккаунты' });
		expect(screen.getByRole('heading', { level: 1 }).textContent?.trim()).toBe('Аккаунты');
	});
});

describe('Card', () => {
	it('секция с заголовком и действием справа', () => {
		render(Card, {
			title: 'Персонаж',
			action: html('<a href="/x">все</a>'),
			children: html('<p>тело</p>')
		});
		const card = screen.getByRole('region', { name: 'Персонаж' });
		expect(card).toHaveTextContent('тело');
		expect(screen.getByRole('link', { name: 'все' })).toBeInTheDocument();
	});
});

describe('Toggle', () => {
	it('switch: клик и пробел зовут onchange(!checked)', async () => {
		const onchange = vi.fn();
		render(Toggle, { checked: false, label: 'Метро', onchange });
		const sw = screen.getByRole('switch', { name: 'Метро' });
		expect(sw).toHaveAttribute('aria-checked', 'false');
		await userEvent.click(sw);
		expect(onchange).toHaveBeenLastCalledWith(true);
		sw.focus();
		await userEvent.keyboard(' ');
		expect(onchange).toHaveBeenCalledTimes(2);
		expect(onchange).toHaveBeenLastCalledWith(true);
	});

	it('включённый — aria-checked true, выключение передаёт false', async () => {
		const onchange = vi.fn();
		render(Toggle, { checked: true, label: 'Сон', onchange });
		const sw = screen.getByRole('switch', { name: 'Сон' });
		expect(sw).toHaveAttribute('aria-checked', 'true');
		await userEvent.click(sw);
		expect(onchange).toHaveBeenCalledWith(false);
	});

	it('disabled — не переключается', async () => {
		const onchange = vi.fn();
		render(Toggle, { checked: false, label: 'Книги', disabled: true, onchange });
		const sw = screen.getByRole('switch', { name: 'Книги' });
		expect(sw).toBeDisabled();
		await userEvent.click(sw);
		sw.focus();
		await userEvent.keyboard(' ');
		expect(onchange).not.toHaveBeenCalled();
	});
});

describe('StatusDot', () => {
	it('подпись доступна', () => {
		render(StatusDot, { tone: 'warn', label: 'метро' });
		const dot = screen.getByLabelText('метро');
		expect(dot).toHaveAttribute('title', 'метро');
	});
});

describe('Tile', () => {
	it('ссылка с бейджем при badge > 0', () => {
		render(Tile, { href: '/a/4/notifications', icon: Bell, label: 'Уведомления', badge: 3 });
		const link = screen.getByRole('link', { name: /Уведомления/ });
		expect(link).toHaveAttribute('href', '/a/4/notifications');
		expect(link).toHaveTextContent('3');
	});

	it('без бейджа при badge = 0', () => {
		render(Tile, { href: '/a/4/notifications', icon: Bell, label: 'Уведомления', badge: 0 });
		const link = screen.getByRole('link', { name: 'Уведомления' });
		expect(link.textContent?.trim()).toBe('Уведомления');
	});

	it('без href — кнопка с onclick', async () => {
		const onclick = vi.fn();
		render(Tile, { icon: Bell, label: 'Тема', onclick });
		await userEvent.click(screen.getByRole('button', { name: 'Тема' }));
		expect(onclick).toHaveBeenCalledOnce();
	});
});

describe('Row', () => {
	it('строка «название — значение» с пометкой устаревшего', () => {
		render(Row, { label: '💵 деньги', stale: true, children: html('<span>$47</span>') });
		expect(screen.getByText('💵 деньги').parentElement).toHaveTextContent('$47');
		expect(screen.getByText('💵 деньги').parentElement).toHaveTextContent('(устарело)');
	});

	it('в списке определений — dt/dd', () => {
		render(Row, { label: 'Статус', dl: true, children: html('<span>готово</span>') });
		expect(screen.getByText('Статус').tagName).toBe('DT');
		expect(screen.getByText('готово').parentElement?.tagName).toBe('DD');
	});
});
