import { describe, expect, it, vi } from 'vitest';
import { deferred, flush } from '$lib/test/deferred';
import { leaveGuard, type LeaveNavigation } from './leave';

function nav(type: LeaveNavigation['type'], to: string | null, willUnload = type === 'leave') {
	const cancel = vi.fn();
	const n: LeaveNavigation = { type, willUnload, to: to === null ? null : { url: new URL(to, 'https://sw.example') }, cancel };
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
});
