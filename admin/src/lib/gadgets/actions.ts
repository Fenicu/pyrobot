import type { AccountApi } from '$lib/api/account';
import { call } from '$lib/api/client';
import type { GadgetsOut, UpSlotKey, UpgradeChoice } from '$lib/api/types';

/** Задача заточки: порции попыток пойдут шагом планировщика вне окон-запретов. */
export function startUpgrade(api: AccountApi, slot: UpSlotKey, target: number, kind: UpgradeChoice): Promise<GadgetsOut> {
	return call(api.POST('/gadgets/upgrade', { body: { slot, target, kind } }));
}

export function stopUpgrade(api: AccountApi): Promise<GadgetsOut> {
	return call(api.POST('/gadgets/upgrade/stop'));
}
