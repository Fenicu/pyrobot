import type { AccountApi } from '$lib/api/account';
import { call } from '$lib/api/client';
import type { ArtifactOut } from '$lib/api/types';
import type { ArtifactKey } from './text';

/** Запрос на сбор: бот запустит его в игре, как только персонаж освободится. */
export function startCollect(api: AccountApi, artifact: ArtifactKey, lotteryMax: boolean): Promise<ArtifactOut> {
	return call(api.POST('/artifact/start', { body: { artifact, lottery_max: lotteryMax } }));
}

export function pauseCollect(api: AccountApi): Promise<ArtifactOut> {
	return call(api.POST('/artifact/pause'));
}

export function resumeCollect(api: AccountApi): Promise<ArtifactOut> {
	return call(api.POST('/artifact/resume'));
}

export function cancelCollect(api: AccountApi): Promise<ArtifactOut> {
	return call(api.POST('/artifact/cancel'));
}

export function adoptCollect(api: AccountApi): Promise<ArtifactOut> {
	return call(api.POST('/artifact/adopt'));
}
