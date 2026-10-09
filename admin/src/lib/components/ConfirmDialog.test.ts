import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { dialogs } from '$lib/stores/confirm.svelte';
import ConfirmDialog from './ConfirmDialog.svelte';

describe('окно подтверждения', () => {
	it('подтверждение кнопкой, текст — только текстом', async () => {
		const user = userEvent.setup();
		render(ConfirmDialog);
		const answer = dialogs.confirm({
			title: 'Отправить «/sells_piper_80»?',
			body: '<b>risky</b>\nдействует 2 минуты',
			confirmText: 'Да, отправить',
			danger: true
		});
		const dialog = await screen.findByRole('dialog', { name: 'Отправить «/sells_piper_80»?' });
		expect(dialog.textContent).toContain('<b>risky</b>');
		expect(dialog.querySelector('b')).toBeNull();
		await user.click(screen.getByRole('button', { name: 'Да, отправить' }));
		expect(await answer).toBe(true);
		expect(screen.queryByRole('dialog')).toBeNull();
	});

	it('Esc — отмена, фокус внутри окна', async () => {
		const user = userEvent.setup();
		render(ConfirmDialog);
		const answer = dialogs.confirm({ title: 'Выйти?' });
		const dialog = await screen.findByRole('dialog');
		expect(dialog.contains(document.activeElement)).toBe(true);
		await user.tab();
		await user.tab();
		await user.tab();
		expect(dialog.contains(document.activeElement)).toBe(true);
		await user.keyboard('{Escape}');
		expect(await answer).toBe(false);
	});

	it('фон на время окна — inert, после закрытия восстановлен', async () => {
		const outside = document.body.appendChild(document.createElement('button'));
		const toasts = document.body.appendChild(document.createElement('div'));
		toasts.setAttribute('data-modal-keep', '');
		const already = document.body.appendChild(document.createElement('div'));
		already.setAttribute('inert', '');
		render(ConfirmDialog);
		const answer = dialogs.confirm({ title: 'Выйти?' });
		await screen.findByRole('dialog');
		expect(outside).toHaveAttribute('inert');
		expect(toasts).not.toHaveAttribute('inert');
		dialogs.answer(null);
		await answer;
		await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
		expect(outside).not.toHaveAttribute('inert');
		// Чужой inert окно не снимает.
		expect(already).toHaveAttribute('inert');
		for (const el of [outside, toasts, already]) el.remove();
	});

	it('поле ввода: обязательная причина kill', async () => {
		const user = userEvent.setup();
		render(ConfirmDialog);
		const answer = dialogs.prompt({
			title: 'Kill',
			input: { label: 'Причина', required: true },
			confirmText: 'Остановить'
		});
		const button = await screen.findByRole('button', { name: 'Остановить' });
		expect(button).toBeDisabled();
		await user.type(screen.getByLabelText('Причина'), '  проверка ');
		await user.keyboard('{Enter}');
		expect(await answer).toBe('проверка');
	});

	it('три варианта: подтвердить, третья кнопка, отмена', async () => {
		const user = userEvent.setup();
		render(ConfirmDialog);
		const req = { title: 'Сохранить?', confirmText: 'Сохранить', altText: 'Не сохранять', cancelText: 'Остаться' };
		for (const [button, expected] of [
			['Сохранить', 'confirm'],
			['Не сохранять', 'alt'],
			['Остаться', null]
		] as const) {
			const answer = dialogs.choose(req);
			const dialog = await screen.findByRole('dialog', { name: 'Сохранить?' });
			expect(
				[...dialog.querySelectorAll('button')].map((b) => b.textContent?.trim()).filter((t) => t !== '')
			).toEqual(expect.arrayContaining(['Остаться', 'Не сохранять', 'Сохранить']));
			await user.click(screen.getByRole('button', { name: button }));
			expect(await answer).toBe(expected);
			await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
		}
	});

	it('без третьей кнопки confirm и prompt — как раньше', async () => {
		render(ConfirmDialog);
		const answer = dialogs.confirm({ title: 'Выйти?' });
		await screen.findByRole('dialog');
		expect(screen.queryByRole('button', { name: 'Не сохранять' })).toBeNull();
		dialogs.answer('');
		expect(await answer).toBe(true);
	});
});
