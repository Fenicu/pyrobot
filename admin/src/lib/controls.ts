import type { AccountApi } from '$lib/api/account';
import { call } from '$lib/api/client';
import type { SettingsPatchOut } from '$lib/api/types';

/** Управление движком с Главной: пауза, kill, режим (режим — через PATCH настроек). */
export async function setPaused(api: AccountApi, paused: boolean): Promise<void> {
	await call(paused ? api.POST('/engine/pause') : api.POST('/engine/resume'));
}

export async function kill(api: AccountApi, reason: string): Promise<void> {
	await call(api.POST('/engine/kill', { body: { reason } }));
}

export async function unkill(api: AccountApi): Promise<void> {
	await call(api.POST('/engine/unkill'));
}

/** Переключение `dry_run` ↔ `live` по свежей версии настроек; в `live` — с `confirm_live` (окно
 * подтверждения показывает вызывающий). */
export async function setMode(api: AccountApi, mode: 'dry_run' | 'live'): Promise<SettingsPatchOut> {
	const current = await call(api.GET('/settings'));
	return call(
		api.PATCH('/settings', {
			body: {
				version: current.version,
				changes: { engine: { mode } },
				confirm_live: mode === 'live'
			}
		})
	);
}
