import { describe, expect, it, vi } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import type { SettingsOut } from '$lib/api/types';
import { deferred, flush } from '$lib/test/deferred';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { SettingsEditor } from './editor.svelte';

const settings = fixture<SettingsOut>('settings');

async function editor(patch: (c: Call) => Response) {
	const fetch = mockFetch((c) => (c.method === 'PATCH' ? patch(c) : json(settings)));
	const api = createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
	const e = new SettingsEditor(api);
	await e.load();
	return { e, fetch };
}

const ok = (c: Call) => {
	const body = JSON.parse(c.body);
	return json({ version: 14, values: settings.values, changed: {}, restart_required: Object.keys(body.changes).includes('chats') ? ['chats.game_chat_id'] : [] });
};

describe('редактор настроек', () => {
	it('сохранение: версия и только изменённые секции', async () => {
		const { e, fetch } = await editor(ok);
		e.set(['food', 'banana_reserve'], 40);
		e.set(['chats', 'game_chat_id'], 1);
		expect(e.changes).toHaveLength(2);
		const res = await e.save(async () => true);
		expect(res).toEqual({ ok: true, version: 14, restartRequired: ['chats.game_chat_id'] });
		expect(JSON.parse(fetch.calls.at(-1)!.body)).toEqual({
			version: 13,
			changes: { food: { banana_reserve: 40 }, chats: { game_chat_id: 1 } },
			confirm_live: false
		});
		expect(e.version).toBe(14);
		expect(e.restartRequired).toEqual(['chats.game_chat_id']);
	});

	it('переход в live — только после подтверждения', async () => {
		const values = { ...settings.values, engine: { ...(settings.values.engine as object), mode: 'dry_run' } };
		const fetch = mockFetch((c) => (c.method === 'PATCH' ? ok(c) : json({ ...settings, values })));
		const api = createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
		const e = new SettingsEditor(api);
		await e.load();
		e.set(['engine', 'mode'], 'live');
		expect(e.goesLive).toBe(true);
		const refuse = vi.fn(async () => false);
		expect(await e.save(refuse)).toEqual({ ok: false, cancelled: true });
		expect(fetch.calls.some((c) => c.method === 'PATCH')).toBe(false);
		await e.save(async () => true);
		expect(JSON.parse(fetch.calls.at(-1)!.body).confirm_live).toBe(true);
	});

	it('409 — «перечитать», 422 — ошибка у поля', async () => {
		const { e } = await editor(() => json({ detail: { code: 'version_conflict', version: 15 } }, 409));
		e.set(['food', 'banana_reserve'], 40);
		const res = await e.save(async () => true);
		expect(res.ok).toBe(false);
		expect(e.conflict).toBe(15);
		const bad = await editor(() =>
			json({ detail: [{ loc: ['body', 'changes', 'sleep', 'duration_h'], msg: 'Input should be less than or equal to 12', type: 'less_than_equal' }] }, 422)
		);
		bad.e.set(['sleep', 'duration_h'], 13);
		await bad.e.save(async () => true);
		expect(bad.e.fieldErrors).toEqual({ 'sleep.duration_h': 'Input should be less than or equal to 12' });
		bad.e.set(['sleep', 'duration_h'], 12);
		expect(bad.e.fieldErrors).toEqual({});
	});

	it('422 вложенного поля — на самый длинный совпавший путь поля, не нашлось — общей ошибкой', async () => {
		const issue = (loc: (string | number)[], msg: string) => ({ loc, msg, type: 'x' });
		const bad = await editor(() =>
			json(
				{
					detail: [
						issue(['body', 'changes', 'strategy', 'deeds', 2], "Input should be 'harvest'"),
						issue(['body', 'changes', 'lottery', 'tickets', 'money', 'constrained-int'], 'Input should be >= 0'),
						issue(['body', 'changes', 'lottery', 'tickets', 'money', "literal['max']"], "Input should be 'max'"),
						issue(['body', 'changes', 'strategy'], 'Value error, focus not in deeds'),
						issue(['body', 'confirm_live'], 'live_requires_confirm')
					]
				},
				422
			)
		);
		bad.e.set(['strategy', 'deeds'], ['harvest', 'job', 'nope']);
		bad.e.set(['lottery', 'tickets', 'money'], -1);
		const res = await bad.e.save(async () => true);
		expect(res).toMatchObject({ ok: false, error: { kind: 'validation' } });
		expect(bad.e.fieldErrors).toEqual({
			'strategy.deeds': "Input should be 'harvest'",
			'lottery.tickets.money': "Input should be >= 0; Input should be 'max'",
			'engine.mode': 'live_requires_confirm'
		});
		expect(bad.e.formErrors).toEqual(['strategy: Value error, focus not in deeds']);
		bad.e.discard();
		expect([bad.e.fieldErrors, bad.e.formErrors]).toEqual([{}, []]);
	});

	it('чужая версия из SSE: без правок — перечитать, с правками — предупредить', async () => {
		const { e, fetch } = await editor(ok);
		e.onEvent({ type: 'settings', id: 'e:1', data: { version: 14, mode: 'live', paused: true, killed: false } });
		await new Promise((r) => setTimeout(r, 0));
		expect(fetch.calls.filter((c) => c.method === 'GET')).toHaveLength(2);
		e.set(['food', 'banana_reserve'], 1);
		e.onEvent({ type: 'settings', id: 'e:2', data: { version: 15, mode: 'live', paused: true, killed: false } });
		expect(e.conflict).toBe(15);
	});
});


describe('правки во время сохранения', () => {
	const setAtPath = (values: Record<string, unknown>, section: string, key: string, value: unknown) => ({
		...values,
		[section]: { ...(values[section] as object), [key]: value }
	});

	async function slow() {
		const reply = deferred<Response>();
		const fetch = mockFetch((c) => (c.method === 'PATCH' ? reply.promise : json(settings)));
		const api = createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
		const e = new SettingsEditor(api);
		await e.load();
		return { e, reply, fetch };
	}

	it('введённое во время PATCH переносится поверх ответа', async () => {
		const { e, reply, fetch } = await slow();
		e.set(['food', 'banana_reserve'], 40);
		const saving = e.save(async () => true);
		await flush();
		expect(e.saving).toBe(true);
		e.set(['sleep', 'duration_h'], 8);
		e.set(['food', 'banana_reserve'], 41);
		reply.resolve(
			json({ version: 14, values: setAtPath(settings.values, 'food', 'banana_reserve', 40), changed: {}, restart_required: [] })
		);
		expect((await saving).ok).toBe(true);
		expect(JSON.parse(fetch.calls.find((c) => c.method === 'PATCH')!.body).changes).toEqual({ food: { banana_reserve: 40 } });
		expect(e.serverValue(['food', 'banana_reserve'])).toBe(40);
		expect(e.value(['food', 'banana_reserve'])).toBe(41);
		expect(e.value(['sleep', 'duration_h'])).toBe(8);
		expect(e.changes.map((p) => p.join('.')).sort()).toEqual(['food.banana_reserve', 'sleep.duration_h']);
	});

	it('свой кадр settings во время сохранения — не конфликт, чужой после — предупреждение', async () => {
		const { e, reply } = await slow();
		e.set(['food', 'banana_reserve'], 40);
		const saving = e.save(async () => true);
		await flush();
		e.onEvent({ type: 'settings', id: 'e:1', data: { version: 14, mode: 'live', paused: false, killed: false } });
		expect(e.conflict).toBeNull();
		reply.resolve(json({ version: 14, values: settings.values, changed: {}, restart_required: [] }));
		await saving;
		expect(e.conflict).toBeNull();

		const next = await slow();
		next.e.set(['food', 'banana_reserve'], 40);
		const again = next.e.save(async () => true);
		await flush();
		next.e.set(['sleep', 'duration_h'], 9);
		next.e.onEvent({ type: 'settings', id: 'e:2', data: { version: 15, mode: 'live', paused: false, killed: false } });
		next.reply.resolve(json({ version: 14, values: settings.values, changed: {}, restart_required: [] }));
		await again;
		expect(next.e.conflict).toBe(15);
	});
});
