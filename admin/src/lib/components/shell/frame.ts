import { getContext, setContext, type Snippet } from 'svelte';
import type { LiveStatus } from '$lib/live/connection.svelte';

/** Экран аккаунта для шапки страницы: имя аккаунта, связь потока и плашки под шапкой. */
export interface AccountFrame {
	title: string;
	live: LiveStatus;
	retryIn: number;
	/** Движок не запущен: поток не открыть — это не обрыв связи. */
	stopped: boolean;
	banners: Snippet;
}

const ACCOUNT_FRAME = Symbol('account-frame');

/** Макет `/a/[account]`: шапки страниц внутри — с именем аккаунта и его плашками. */
export function setAccountFrame(get: () => AccountFrame | null): void {
	setContext(ACCOUNT_FRAME, get);
}

/** Вне экранов аккаунта — null (вызывать при создании компонента). */
export function accountFrame(): () => AccountFrame | null {
	return getContext<(() => AccountFrame | null) | undefined>(ACCOUNT_FRAME) ?? (() => null);
}

/** Контекст для `render(…, { context })` в тестах. */
export function accountFrameContext(frame: AccountFrame): Map<symbol, () => AccountFrame | null> {
	return new Map([[ACCOUNT_FRAME, () => frame]]);
}
