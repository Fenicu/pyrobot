import { describe, expect, it } from 'vitest';
import type { CommandOut } from '$lib/api/types';
import { commandResult } from './commands';

const out = (patch: Partial<CommandOut>): CommandOut => ({
	action_id: 1,
	status: 'rejected',
	reason: '',
	answer: null,
	...patch
});

describe('commandResult', () => {
	it('отказ без связи с Telegram — по-русски', () => {
		expect(commandResult(out({ reason: 'tg_unreachable' }))).toEqual({
			text: 'Отклонено: нет связи с Telegram',
			kind: 'error'
		});
	});
});
