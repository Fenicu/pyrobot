import type { BeforeNavigate } from '@sveltejs/kit';

export type LeaveNavigation = Pick<BeforeNavigate, 'type' | 'willUnload' | 'cancel'> & {
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
 * подтверждения и тот же переход после «да». */
export function leaveGuard({ dirty, confirm, go }: LeaveDeps): (nav: LeaveNavigation) => void {
	let allowed = false;
	return (nav) => {
		if (allowed || !dirty()) return;
		nav.cancel();
		const to = nav.to;
		if (nav.type === 'leave' || to === null) return;
		void confirm().then((ok) => {
			if (!ok) return;
			allowed = true;
			go(to.url, nav.willUnload);
		});
	};
}
