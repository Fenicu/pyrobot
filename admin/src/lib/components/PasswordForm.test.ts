import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { createApi } from '$lib/api/client';
import { json, mockFetch } from '$lib/test/fetch';
import PasswordForm from './PasswordForm.svelte';

describe('Смена пароля', () => {
	it('проверка на клиенте, неверный текущий, успех', async () => {
		const user = userEvent.setup();
		let ok = false;
		const fetch = mockFetch(() => (ok ? new Response(null, { status: 204 }) : json({ detail: 'invalid current password' }, 403)));
		const ondone = vi.fn();
		render(PasswordForm, {
			api: createApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, fetch),
			ondone
		});
		await user.type(screen.getByLabelText('Текущий пароль'), 'old-password');
		await user.type(screen.getByLabelText('Новый пароль (не короче 12)'), 'short');
		expect(screen.getByText('не короче 12 символов')).toBeInTheDocument();
		await user.type(screen.getByLabelText('Новый пароль (не короче 12)'), '-but-long-now');
		await user.type(screen.getByLabelText('Новый пароль ещё раз'), 'short-but-long-no');
		expect(screen.getByRole('button', { name: 'Сменить пароль' })).toBeDisabled();
		await user.type(screen.getByLabelText('Новый пароль ещё раз'), 'w');
		await user.click(screen.getByRole('button', { name: 'Сменить пароль' }));
		expect(await screen.findByRole('alert')).toHaveTextContent('Неверный текущий пароль');
		ok = true;
		await user.click(screen.getByRole('button', { name: 'Сменить пароль' }));
		await vi.waitFor(() => expect(ondone).toHaveBeenCalled());
		expect(JSON.parse(fetch.calls.at(-1)!.body)).toEqual({ current: 'old-password', new: 'short-but-long-now' });
	});
});
