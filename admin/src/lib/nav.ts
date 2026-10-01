import { getContext, setContext, type Component } from 'svelte';
import Bell from '@lucide/svelte/icons/bell';
import CalendarDays from '@lucide/svelte/icons/calendar-days';
import ChartLine from '@lucide/svelte/icons/chart-line';
import Gamepad2 from '@lucide/svelte/icons/gamepad-2';
import House from '@lucide/svelte/icons/house';
import KeyRound from '@lucide/svelte/icons/key-round';
import ScrollText from '@lucide/svelte/icons/scroll-text';
import Send from '@lucide/svelte/icons/send';
import Settings from '@lucide/svelte/icons/settings';
import TrainFront from '@lucide/svelte/icons/train-front';
import Users from '@lucide/svelte/icons/users';

export interface NavItem {
	href: string;
	label: string;
	/** Короткая подпись для нижней панели телефона. */
	short?: string;
	icon: Component<{ class?: string; 'aria-hidden'?: boolean | 'true' }>;
	badge?: 'unread';
}

/** Экран аккаунта: `/a/<id>` и путь раздела (`''` — главная аккаунта). */
export function accountHref(id: number, path: string): string {
	return `/a/${id}${path}`;
}

/** Нижняя панель телефона и верх меню ПК: разделы аккаунта `id`. */
export function mainNav(id: number): NavItem[] {
	return [
		{ href: accountHref(id, ''), label: 'Главная', icon: House },
		{ href: accountHref(id, '/journal'), label: 'Журнал', icon: ScrollText },
		{ href: accountHref(id, '/control'), label: 'Управление', short: 'Управл.', icon: Gamepad2 },
		{ href: accountHref(id, '/metro'), label: 'Метро', icon: TrainFront }
	];
}

/** «Ещё» на телефоне, продолжение меню на ПК: разделы аккаунта (если он есть) и общие. */
export function moreNav(id: number | null): NavItem[] {
	const account: NavItem[] =
		id === null
			? []
			: [
					{ href: accountHref(id, '/daily'), label: 'Итоги', icon: CalendarDays },
					{ href: accountHref(id, '/metrics'), label: 'Метрики', icon: ChartLine },
					{ href: accountHref(id, '/settings'), label: 'Настройки', icon: Settings },
					{ href: accountHref(id, '/notifications'), label: 'Уведомления', icon: Bell, badge: 'unread' },
					{ href: accountHref(id, '/telegram'), label: 'Telegram', icon: Send }
				];
	return [
		...account,
		{ href: '/accounts', label: 'Аккаунты', icon: Users },
		{ href: '/password', label: 'Смена пароля', icon: KeyRound }
	];
}

const ACCOUNT_ROOT = /^\/a\/\d+$/;
const IN_ACCOUNT = /^\/a\/\d+(\/.*)?$/;

/** Главная (`/`, `/a/<id>`) активна только сама, раздел — и на вложенных путях. */
export function isActive(pathname: string, href: string): boolean {
	return href === '/' || ACCOUNT_ROOT.test(href)
		? pathname === href
		: pathname === href || pathname.startsWith(`${href}/`);
}

/** Переключение аккаунта: тот же раздел другого аккаунта (без query — номера у каждого свои);
 * с общих экранов — главная аккаунта. */
export function switchHref(pathname: string, id: number): string {
	return accountHref(id, IN_ACCOUNT.exec(pathname)?.[1] ?? '');
}

/** Номер аккаунта из адреса; не число — null. */
export function parseAccount(raw: string | undefined): number | null {
	if (raw === undefined || !/^[1-9]\d*$/.test(raw)) return null;
	const id = Number(raw);
	return Number.isSafeInteger(id) ? id : null;
}

const LAST_ACCOUNT = 'pyrobot.account';

/** Последний открытый аккаунт (localStorage; без него — не помним). */
export function lastAccount(): number | null {
	try {
		return parseAccount(localStorage.getItem(LAST_ACCOUNT) ?? undefined);
	} catch {
		return null;
	}
}

export function rememberAccount(id: number): void {
	try {
		localStorage.setItem(LAST_ACCOUNT, String(id));
	} catch {
		// без localStorage «/» ведёт в первый аккаунт
	}
}

/** Аккаунт по умолчанию: последний открытый, если он ещё в списке, иначе первый; нет — null. */
export function pickAccount(list: readonly { id: number }[], last: number | null): number | null {
	return list.find((a) => a.id === last)?.id ?? list[0]?.id ?? null;
}

/** Куда ведёт `/`: аккаунт по умолчанию, а без аккаунтов — их список. */
export function homeHref(list: readonly { id: number }[], last: number | null): string {
	const id = pickAccount(list, last);
	return id === null ? '/accounts' : accountHref(id, '');
}

// Экраны до аккаунтов: старые ссылки и закладки.
const LEGACY = new Set([
	'/journal',
	'/control',
	'/daily',
	'/metrics',
	'/metro',
	'/notifications',
	'/settings',
	'/telegram'
]);

export function isLegacy(pathname: string): boolean {
	return LEGACY.has(pathname);
}

/** Старая ссылка — тот же экран аккаунта `id` с теми же query и якорем; аккаунтов нет — их список;
 * не старая — null. */
export function legacyHref(url: URL, id: number | null): string | null {
	if (!LEGACY.has(url.pathname)) return null;
	return id === null ? '/accounts' : accountHref(id, url.pathname + url.search + url.hash);
}

const SCREEN_ACCOUNT = Symbol('screen-account');

/** Макет `/a/[account]`: аккаунт экрана — для ссылок в глубине компонентов. */
export function setScreenAccount(id: () => number | null): void {
	setContext(SCREEN_ACCOUNT, id);
}

/** Ссылки на разделы аккаунта, открытого на экране (вызывать при создании компонента). Вне экранов
 * аккаунта — без ссылки. */
export function screenHref(): (path: string) => string | undefined {
	const get = getContext<(() => number | null) | undefined>(SCREEN_ACCOUNT);
	return (path) => {
		const id = get?.() ?? null;
		return id === null ? undefined : accountHref(id, path);
	};
}

/** Контекст для `render(…, { context })` в тестах компонентов. */
export function screenAccountContext(id: number): Map<symbol, () => number | null> {
	return new Map([[SCREEN_ACCOUNT, () => id]]);
}

const BASE = 'http://app.invalid';
// Без возврата: главная — и так по умолчанию, после смены пароля форма не нужна, список аккаунтов —
// в меню.
const NO_RETURN = new Set(['/', '/login', '/password', '/accounts']);

/** Вход с возвратом на текущую страницу (`?next=`); со страницы входа — её же `next`. */
export function loginHref(url: URL): string {
	if (url.pathname === '/login') return `/login${url.search}`;
	if (NO_RETURN.has(url.pathname)) return '/login';
	return `/login?next=${encodeURIComponent(url.pathname + url.search + url.hash)}`;
}

/** Куда вернуться после входа: только путь этого же приложения, иначе — главная. */
export function safeNext(raw: string | null): string {
	if (!raw || !raw.startsWith('/')) return '/';
	let url: URL;
	try {
		url = new URL(raw, BASE);
	} catch {
		return '/';
	}
	const path = url.pathname + url.search + url.hash;
	if (url.origin !== BASE || path.startsWith('//') || url.pathname === '/login') return '/';
	return path;
}
