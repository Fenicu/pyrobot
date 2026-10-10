import { cleanup, render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { tick } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { deferred, flush } from '$lib/test/deferred';
import { updated } from '$lib/test/updated.svelte';
import UpdateGuard from './update-guard.test.svelte';
import UpdateBanner from './UpdateBanner.svelte';

const r = vi.hoisted(() => ({ router: null as unknown as ReturnType<typeof import('$lib/test/router').fakeRouter> }));

vi.mock('$app/state', async () => ({ updated: (await import('$lib/test/updated.svelte')).updated }));
vi.mock('$app/navigation', async () => {
	const { fakeRouter } = await import('$lib/test/router');
	r.router = fakeRouter('https://sw.example/a/1/settings');
	return {
		beforeNavigate: (fn: never) => r.router.beforeNavigate(fn),
		onNavigate: (fn: never) => r.router.onNavigate(fn),
		goto: (href: string) => r.router.goto(href)
	};
});

let loc: { href: string; reload: ReturnType<typeof vi.fn> };

beforeEach(() => {
	loc = { href: 'https://sw.example/a/1/settings', reload: vi.fn() };
	vi.stubGlobal('location', loc);
	r.router.url = new URL(loc.href);
});

afterEach(() => {
	cleanup();
	vi.unstubAllGlobals();
	updated.current = false;
	updated.check.mockClear();
});

function setVisibility(state: DocumentVisibilityState) {
	Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state });
	document.dispatchEvent(new Event('visibilitychange'));
}

describe('новая версия админки', () => {
	it('версия та же — плашки нет', () => {
		render(UpdateBanner);
		expect(screen.queryByText('Вышла новая версия')).toBeNull();
	});

	it('вышла новая — плашка со «Обновить», кнопка перезагружает страницу', async () => {
		render(UpdateBanner);
		updated.current = true;
		await tick();
		const banner = screen.getByRole('status');
		expect(banner).toHaveTextContent('Вышла новая версия');
		await userEvent.setup().click(screen.getByRole('button', { name: 'Обновить' }));
		expect(loc.reload).toHaveBeenCalledTimes(1);
	});

	it('возврат во вкладку (приложение снова на экране) — проверка версии; уход в фон — нет', () => {
		render(UpdateBanner);
		setVisibility('hidden');
		expect(updated.check).not.toHaveBeenCalled();
		setVisibility('visible');
		expect(updated.check).toHaveBeenCalledTimes(1);
		// Уже нашли — повторно не спрашиваем.
		updated.current = true;
		setVisibility('visible');
		expect(updated.check).toHaveBeenCalledTimes(1);
	});

	it('после размонтирования вкладка версию не проверяет', () => {
		render(UpdateBanner);
		cleanup();
		setVisibility('visible');
		expect(updated.check).not.toHaveBeenCalled();
	});

	it('переход на другую страницу при новой версии — полная загрузка нужного адреса', () => {
		render(UpdateBanner);
		updated.current = true;
		r.router.navigate('/a/1/journal?type=action');
		expect(loc.href).toBe('https://sw.example/a/1/journal?type=action');
	});

	it('без новой версии переход — обычный, внутри приложения', () => {
		render(UpdateBanner);
		r.router.navigate('/a/1/journal');
		expect(loc.href).toBe('https://sw.example/a/1/settings');
	});

	it('смена только query (вкладка настроек) страницу не перезагружает', () => {
		render(UpdateBanner);
		updated.current = true;
		r.router.navigate('/a/1/settings?tab=battle');
		expect(loc.href).toBe('https://sw.example/a/1/settings');
	});

	for (const guardFirst of [true, false]) {
		it(`несохранённые настройки: без «Уйти» перезагрузки нет (защита ${guardFirst ? 'раньше' : 'позже'} плашки)`, async () => {
			const answer = deferred<boolean>();
			const go = vi.fn((url: URL) => r.router.navigate(url, 'goto'));
			render(UpdateGuard, { guardFirst, confirm: () => answer.promise, go });
			updated.current = true;
			expect(r.router.navigate('/a/1/journal')).toBe(false);
			expect(loc.href).toBe('https://sw.example/a/1/settings');
			answer.resolve(true);
			await flush();
			// «Уйти» — тот же переход, уже полной загрузкой новой версии.
			expect(go).toHaveBeenCalledTimes(1);
			expect(loc.href).toBe('https://sw.example/a/1/journal');
		});
	}

	it('несохранённые настройки, «Остаться» — ни перехода, ни перезагрузки', async () => {
		const go = vi.fn();
		render(UpdateGuard, { guardFirst: false, confirm: async () => false, go });
		updated.current = true;
		r.router.navigate('/a/1/journal');
		await flush();
		expect(go).not.toHaveBeenCalled();
		expect(loc.href).toBe('https://sw.example/a/1/settings');
		expect(loc.reload).not.toHaveBeenCalled();
	});
});
