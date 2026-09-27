import { errorText, type ApiError } from '$lib/api/errors';
import type { ConfirmRequest } from '$lib/stores/confirm.svelte';

/** Выход с повтором: пока сервер не подтвердил выход, сессия считается открытой, а пользователь
 * решает — повторить или остаться. true — вышли. */
export async function signOutWithRetry(
	session: { signOut(): Promise<ApiError | null> },
	ask: { confirm(req: ConfirmRequest): Promise<boolean> }
): Promise<boolean> {
	for (;;) {
		const error = await session.signOut();
		if (error === null) return true;
		const retry = await ask.confirm({
			title: 'Выход не выполнен',
			body: `${errorText(error)}. Сессия на сервере ещё открыта.`,
			confirmText: 'Повторить',
			cancelText: 'Остаться'
		});
		if (!retry) return false;
	}
}
