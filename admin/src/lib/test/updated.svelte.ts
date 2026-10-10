import { vi } from 'vitest';

/** Подделка `updated` из `$app/state`: новую версию «находит» сам тест. */
export const updated = $state({
	current: false,
	check: vi.fn(async () => false)
});
