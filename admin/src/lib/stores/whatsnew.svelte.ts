import { entriesSince, parseChangelog, type ChangelogEntry } from '$lib/changelog';

/** Версия сборки: git-тег без «v», без тега — `0.0.0-dev` (vite.config.ts). */
export const APP_VERSION: string = __APP_VERSION__;

export const SEEN_VERSION_KEY = 'pyrobot.seen-version';
const RELEASE = /^\d+\.\d+\.\d+$/;

function readSeen(): string | null {
	try {
		return localStorage.getItem(SEEN_VERSION_KEY);
	} catch {
		return null;
	}
}

function writeSeen(version: string): boolean {
	try {
		localStorage.setItem(SEEN_VERSION_KEY, version);
		return true;
	} catch {
		return false;
	}
}

/** Окно «Что нового»: записи CHANGES.rst между версией, которую этот браузер уже видел, и
 * установленной. Виденная версия — свойство браузера, а не учётки: хранится в localStorage. */
export class WhatsNew {
	entries = $state<ChangelogEntry[]>([]);
	open = $state(false);
	readonly #version: string;
	readonly #all: ChangelogEntry[];

	constructor(version: string = APP_VERSION, all: ChangelogEntry[] = parseChangelog()) {
		this.#version = version;
		this.#all = all;
	}

	/** Первый визит только запоминает версию. Версия отмечается виденной сразу, а не по «Понятно»:
	 * иначе окно возвращалось бы после каждой перезагрузки. Сборка без тега и браузер без
	 * localStorage окна не показывают. */
	check(): void {
		if (!RELEASE.test(this.#version)) return;
		const seen = readSeen();
		if (seen === this.#version || !writeSeen(this.#version)) return;
		this.entries = entriesSince(seen, this.#version, this.#all);
		this.open = this.entries.length > 0;
	}

	dismiss(): void {
		this.open = false;
	}
}

export const whatsNew = new WhatsNew();
