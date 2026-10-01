import type { ParamMatcher } from '@sveltejs/kit';
import { isLegacy } from '$lib/nav';

/** Старые пути без аккаунта (`/journal` и т. п.): у них свой маршрут — иначе холодная загрузка пишет
 * в консоль «Not found», а `goto` на такой путь (вход с `next=/journal`) — полная перезагрузка. */
export const match = ((param) => isLegacy(`/${param}`)) satisfies ParamMatcher;
