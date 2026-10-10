import { describe, expect, it, vi } from 'vitest';
import { deferred, flush } from '$lib/test/deferred';
import { leaveGuard, type LeaveNavigation } from './leave';

function nav(type: LeaveNavigation['type'], to: string | null, willUnload = type === 'leave', from: string | null = null) {
	const cancel = vi.fn();
	const at = (path: string | null) => (path === null ? null : { url: new URL(path, 'https://sw.example') });
	const n: LeaveNavigation = { type, willUnload, from: at(from), to: at(to), cancel };
	return { n, cancel };
}

describe('уход с несохранёнными настройками', () => {
	it('без правок — переход без вопросов', () => {
		const confirm = vi.fn(async () => true);
		const guard = leaveGuard({ dirty: () => false, confirm, go: vi.fn() });
		const { n, cancel } = nav('link', '/journal');
		guard(n);
		expect(cancel).not.toHaveBeenCalled();
		expect(confirm).not.toHaveBeenCalled();
	});

	it('закрытие вкладки или уход с сайта — окно браузера (beforeunload)', () => {
		const confirm = vi.fn(async () => true);
		const guard = leaveGuard({ dirty: () => true, confirm, go: vi.fn() });
		const { n, cancel } = nav('leave', null);
		guard(n);
		expect(cancel).toHaveBeenCalledTimes(1);
		expect(confirm).not.toHaveBeenCalled();
	});

	it('переход внутри приложения — своё окно, после «да» — тот же переход без повторного вопроса', async () => {
		const answer = deferred<boolean>();
		const confirm = vi.fn(() => answer.promise);
		const go = vi.fn();
		const guard = leaveGuard({ dirty: () => true, confirm, go });
		const first = nav('link', '/journal?type=action');
		guard(first.n);
		expect(first.cancel).toHaveBeenCalledTimes(1);
		expect(go).not.toHaveBeenCalled();
		answer.resolve(true);
		await flush();
		expect(go).toHaveBeenCalledWith(new URL('https://sw.example/journal?type=action'), false);
		const again = nav('goto', '/journal?type=action');
		guard(again.n);
		expect(again.cancel).not.toHaveBeenCalled();
	});

	it('«нет» — остаёмся, следующий переход снова спрашивает', async () => {
		const confirm = vi.fn(async () => false);
		const go = vi.fn();
		const guard = leaveGuard({ dirty: () => true, confirm, go });
		guard(nav('popstate', '/').n);
		await flush();
		expect(go).not.toHaveBeenCalled();
		const again = nav('link', '/metro');
		guard(again.n);
		expect(again.cancel).toHaveBeenCalledTimes(1);
		expect(confirm).toHaveBeenCalledTimes(2);
	});

	it('разрешение одноразовое: после повторённого перехода новый уход с правками снова спрашивает', async () => {
		const confirm = vi.fn(async () => true);
		const go = vi.fn();
		const guard = leaveGuard({ dirty: () => true, confirm, go });
		guard(nav('link', '/a/1').n);
		await flush();
		expect(go).toHaveBeenCalledTimes(1);
		// Тот же адрес: страница не пересоздаётся, обработчик живёт дальше.
		const repeated = nav('goto', '/a/1');
		guard(repeated.n);
		expect(repeated.cancel).not.toHaveBeenCalled();
		const next = nav('link', '/a/1/journal');
		guard(next.n);
		expect(next.cancel).toHaveBeenCalledTimes(1);
		expect(confirm).toHaveBeenCalledTimes(2);
	});

	it('смена query на той же странице (вкладка настроек) — без вопросов, черновик остаётся', () => {
		const confirm = vi.fn(async () => true);
		const guard = leaveGuard({ dirty: () => true, confirm, go: vi.fn() });
		const same = nav('goto', '/a/1/settings?tab=battle', false, '/a/1/settings?tab=food');
		guard(same.n);
		expect(same.cancel).not.toHaveBeenCalled();
		expect(confirm).not.toHaveBeenCalled();
		// Тот же раздел другого аккаунта — уже уход.
		const other = nav('link', '/a/2/settings?tab=battle', false, '/a/1/settings?tab=battle');
		guard(other.n);
		expect(other.cancel).toHaveBeenCalledTimes(1);
	});
});
