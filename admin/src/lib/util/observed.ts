import type { Observed, PublicState } from '$lib/api/types';

/** Значение наблюдаемого поля снимка или null (нет ключа, не наблюдалось). */
export function val<K extends keyof PublicState>(
	state: PublicState,
	key: K
): NonNullable<PublicState[K]> extends Observed<infer T> ? T | null : never {
	const obs = state[key] as Observed<unknown> | null | undefined;
	return (obs && typeof obs === 'object' && 'value' in obs ? obs.value : null) as never;
}

/** Момент наблюдения поля. */
export function seenAt(state: PublicState, key: keyof PublicState): string | null {
	const obs = state[key] as Observed<unknown> | null | undefined;
	return obs && typeof obs === 'object' && 'at' in obs ? obs.at : null;
}
