import type { BeforeNavigate } from '@sveltejs/kit';

export type LeaveNavigation = Pick<BeforeNavigate, 'type' | 'willUnload' | 'cancel'> & {
	from: { url: URL } | null;
	to: { url: URL } | null;
};

interface LeaveDeps {
	/** Есть несохранённые правки. */
	dirty: () => boolean;
	confirm: () => Promise<boolean>;
	/** Повторить отменённый переход; `unload` — уход со страницы приложения целиком. */
	go: (url: URL, unload: boolean) => void;
}

/** Обработчик `beforeNavigate` страницы с черновиком. Закрытие вкладки и перезагрузка (`leave`) —
 * отмена, и SvelteKit показывает окно браузера `beforeunload`; другой переход — своё окно
 * подтверждения и тот же переход после «да». Разрешение — только на этот повторный переход:
 * страница может пережить его (тот же адрес), и следующий уход снова спрашивает. */
export function leaveGuard({ dirty, confirm, go }: LeaveDeps): (nav: LeaveNavigation) => void {
	let allowed = false;
	return (nav) => {
		if (allowed) {
			allowed = false;
			return;
		}
		const to = nav.to;
		// Меняется только query (вкладка, фильтр) — страница с черновиком остаётся.
		if (nav.type !== 'leave' && to !== null && nav.from?.url.pathname === to.url.pathname) return;
		if (!dirty()) return;
		nav.cancel();
		if (nav.type === 'leave' || to === null) return;
		void confirm().then((ok) => {
			if (!ok) return;
			allowed = true;
			go(to.url, nav.willUnload);
		});
	};
}
