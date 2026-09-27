import { describe, expect, it, vi } from 'vitest';
import type { ApiError } from '$lib/api/errors';
import type { ConfirmRequest } from '$lib/stores/confirm.svelte';
import { deferred, flush } from '$lib/test/deferred';
import { signOutWithRetry } from './logout';

describe('выход из админки', () => {
	it('ошибка — окно с повтором; повтор до успеха', async () => {
		const answers = [deferred<ApiError | null>(), deferred<ApiError | null>()];
		const signOut = vi.fn(() => answers[signOut.mock.calls.length - 1]!.promise);
		const confirm = vi.fn(async (_req: ConfirmRequest) => true);
		const done = signOutWithRetry({ signOut }, { confirm });
		answers[0]!.resolve({ kind: 'network', status: 0, message: 'offline' });
		await flush();
		expect(confirm).toHaveBeenCalledWith(
			expect.objectContaining({ title: 'Выход не выполнен', confirmText: 'Повторить' })
		);
		expect(confirm.mock.calls[0]?.[0].body).toContain('Нет связи с сервером');
		answers[1]!.resolve(null);
		expect(await done).toBe(true);
		expect(signOut).toHaveBeenCalledTimes(2);
	});

	it('отказ от повтора — сессия остаётся', async () => {
		const signOut = vi.fn(async (): Promise<ApiError | null> => ({ kind: 'csrf', status: 403 }));
		expect(await signOutWithRetry({ signOut }, { confirm: async () => false })).toBe(false);
		expect(signOut).toHaveBeenCalledTimes(1);
	});
});
