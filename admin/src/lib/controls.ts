import { call, type Api } from '$lib/api/client';
import type { SettingsPatchOut } from '$lib/api/types';

/** Управление движком с Главной: пауза, kill, режим (режим — через PATCH настроек). */
export async function setPaused(api: Api, paused: boolean): Promise<void> {
	await call(paused ? api.POST('/api/v1/engine/pause') : api.POST('/api/v1/engine/resume'));
}

export async function kill(api: Api, reason: string): Promise<void> {
	await call(api.POST('/api/v1/engine/kill', { body: { reason } }));
}

export async function unkill(api: Api): Promise<void> {
	await call(api.POST('/api/v1/engine/unkill'));
}

/** Переключение `dry_run` ↔ `live` по свежей версии настроек; в `live` — с `confirm_live` (окно
 * подтверждения показывает вызывающий). */
export async function setMode(api: Api, mode: 'dry_run' | 'live'): Promise<SettingsPatchOut> {
	const current = await call(api.GET('/api/v1/settings'));
	return call(
		api.PATCH('/api/v1/settings', {
			body: {
				version: current.version,
				changes: { engine: { mode } },
				confirm_live: mode === 'live'
			}
		})
	);
}
