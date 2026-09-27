import type { Component } from 'svelte';
import Bell from '@lucide/svelte/icons/bell';
import ChartLine from '@lucide/svelte/icons/chart-line';
import Gamepad2 from '@lucide/svelte/icons/gamepad-2';
import House from '@lucide/svelte/icons/house';
import KeyRound from '@lucide/svelte/icons/key-round';
import ScrollText from '@lucide/svelte/icons/scroll-text';
import Send from '@lucide/svelte/icons/send';
import Settings from '@lucide/svelte/icons/settings';
import TrainFront from '@lucide/svelte/icons/train-front';

export interface NavItem {
	href: string;
	label: string;
	/** Короткая подпись для нижней панели телефона. */
	short?: string;
	icon: Component<{ class?: string; 'aria-hidden'?: boolean | 'true' }>;
	badge?: 'unread';
}

/** Нижняя панель телефона и верх меню ПК. */
export const MAIN_NAV: NavItem[] = [
	{ href: '/', label: 'Главная', icon: House },
	{ href: '/journal', label: 'Журнал', icon: ScrollText },
	{ href: '/control', label: 'Управление', short: 'Управл.', icon: Gamepad2 },
	{ href: '/metro', label: 'Метро', icon: TrainFront }
];

/** «Ещё» на телефоне, продолжение меню на ПК. */
export const MORE_NAV: NavItem[] = [
	{ href: '/metrics', label: 'Метрики', icon: ChartLine },
	{ href: '/settings', label: 'Настройки', icon: Settings },
	{ href: '/notifications', label: 'Уведомления', icon: Bell, badge: 'unread' },
	{ href: '/telegram', label: 'Telegram', icon: Send },
	{ href: '/password', label: 'Смена пароля', icon: KeyRound }
];

export function isActive(pathname: string, href: string): boolean {
	return href === '/' ? pathname === '/' : pathname === href || pathname.startsWith(`${href}/`);
}
