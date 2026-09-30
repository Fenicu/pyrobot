import type { AccountApi } from '$lib/api/account';
import { call } from '$lib/api/client';
import { ApiFailure, errorText, type ApiError, type ValidationIssue } from '$lib/api/errors';
import type { SettingsOut } from '$lib/api/types';
import type { LiveEvent } from '$lib/live/sse';
import { SECTION_ORDER } from './labels';
import {
	buildChanges,
	changedPaths,
	getAt,
	leaves,
	pathKey,
	same,
	sectionsOf,
	setAt,
	type Json,
	type JsonSchema,
	type Path,
	type Section
} from './schema';

export type SaveResult =
	| { ok: true; version: number; restartRequired: string[] }
	| { ok: false; error: ApiError }
	| { ok: false; cancelled: true };

const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

function longestPrefix(paths: Path[], loc: string[]): Path | null {
	let best: Path | null = null;
	for (const p of paths) {
		if (p.length <= loc.length && p.every((k, i) => k === loc[i]) && p.length > (best?.length ?? 0)) best = p;
	}
	return best;
}

/** Редактор настроек: черновик поверх значений сервера, diff по листьям схемы, сохранение с
 * версией; 409 — «перечитать», 422 — ошибки у полей. */
export class SettingsEditor {
	server = $state<SettingsOut | null>(null);
	draft = $state<Json>({});
	saving = $state(false);
	loadError = $state<ApiError | null>(null);
	/** Версия на сервере новее нашей (409 или кадр settings при несохранённых правках). */
	conflict = $state<number | null>(null);
	fieldErrors = $state<Record<string, string>>({});
	/** Ошибки 422, которые не легли ни на одно поле формы: «путь: текст». */
	formErrors = $state<string[]>([]);
	/** Сохранено, но читается только при старте процесса. */
	restartRequired = $state<string[]>([]);
	#api: AccountApi;
	#seenDuringSave: number | null = null;

	constructor(api: AccountApi) {
		this.#api = api;
	}

	readonly sections: Section[] = $derived.by(() => {
		const schema = this.server?.schema as JsonSchema | undefined;
		if (!schema) return [];
		const all = sectionsOf(schema);
		const rank = (n: string) => (SECTION_ORDER.includes(n) ? SECTION_ORDER.indexOf(n) : 100);
		return all.sort((a, b) => rank(a.name) - rank(b.name));
	});

	readonly changes: Path[] = $derived(
		this.server ? changedPaths(this.sections, this.server.values, this.draft) : []
	);

	get version(): number | null {
		return this.server?.version ?? null;
	}

	async load(): Promise<void> {
		try {
			const out = await call(this.#api.GET('/settings'));
			this.server = out;
			this.draft = clone(out.values);
			this.conflict = null;
			this.fieldErrors = {};
			this.formErrors = [];
			this.loadError = null;
		} catch (e) {
			if (e instanceof ApiFailure) this.loadError = e.error;
		}
	}

	value(path: Path): Json {
		return getAt(this.draft, path);
	}

	serverValue(path: Path): Json {
		return getAt(this.server?.values, path);
	}

	defaultValue(path: Path): Json {
		return getAt(this.server?.defaults, path);
	}

	isChanged(path: Path): boolean {
		return !same(this.serverValue(path), this.value(path));
	}

	isDefault(path: Path): boolean {
		return same(this.defaultValue(path), this.value(path));
	}

	set(path: Path, value: Json): void {
		this.draft = setAt(this.draft, path, value);
		const key = pathKey(path);
		if (key in this.fieldErrors) {
			const { [key]: _, ...rest } = this.fieldErrors;
			this.fieldErrors = rest;
		}
	}

	discard(): void {
		if (this.server) this.draft = clone(this.server.values);
		this.fieldErrors = {};
		this.formErrors = [];
	}

	/** Переход в live: черновик меняет `engine.mode` с dry_run на live. */
	get goesLive(): boolean {
		return this.serverValue(['engine', 'mode']) !== 'live' && this.value(['engine', 'mode']) === 'live';
	}

	async save(confirmLive: () => Promise<boolean>): Promise<SaveResult> {
		const server = this.server;
		if (!server || this.changes.length === 0) return { ok: false, cancelled: true };
		const live = this.goesLive;
		if (live && !(await confirmLive())) return { ok: false, cancelled: true };
		this.saving = true;
		this.#seenDuringSave = null;
		// Снимок отправленного черновика: правки, введённые во время запроса, — это отличия
		// текущего черновика от него, они переносятся поверх ответа.
		const sent = clone(this.draft);
		try {
			const out = await call(
				this.#api.PATCH('/settings', {
					body: {
						version: server.version,
						changes: buildChanges(sent, this.changes),
						confirm_live: live
					}
				})
			);
			const later = changedPaths(this.sections, sent, this.draft);
			let draft: Json = clone(out.values);
			for (const path of later) draft = setAt(draft, path, getAt(this.draft, path));
			this.server = { ...server, version: out.version, values: out.values };
			this.draft = draft;
			this.restartRequired = out.restart_required;
			this.fieldErrors = {};
			this.formErrors = [];
			this.conflict = null;
			// Кадр settings, пришедший во время запроса, новее сохранённой версии — чужое изменение.
			const seen = this.#seenDuringSave;
			if (seen !== null && seen > out.version) this.#external(seen);
			return { ok: true, version: out.version, restartRequired: out.restart_required };
		} catch (e) {
			if (!(e instanceof ApiFailure)) throw e;
			const error = e.error;
			if (error.kind === 'version_conflict') this.conflict = error.version;
			if (error.kind === 'validation') this.#placeIssues(error.issues);
			return { ok: false, error };
		} finally {
			this.saving = false;
			this.#seenDuringSave = null;
		}
	}

	/** Ошибка 422 — у поля с самым длинным путём, который начинает `loc` (элемент списка
	 * `strategy.deeds.2` → `strategy.deeds`, ветка union `max_or_int` → само поле); не нашлось —
	 * в `formErrors`. `loc`: ["body", "changes", секция, поле…] или ["body", "confirm_live"]. */
	#placeIssues(issues: ValidationIssue[]): void {
		const paths = this.sections.flatMap((s) => leaves(s.fields)).map((f) => f.path);
		const fields: Record<string, string[]> = {};
		const rest: string[] = [];
		for (const issue of issues) {
			const loc =
				issue.loc[1] === 'changes'
					? issue.loc.slice(2).map(String)
					: issue.loc[1] === 'confirm_live'
						? ['engine', 'mode']
						: null;
			const path = loc === null ? null : longestPrefix(paths, loc);
			if (path === null) {
				const where = issue.loc.slice(issue.loc[1] === 'changes' ? 2 : 1).join('.');
				rest.push(where ? `${where}: ${issue.msg}` : issue.msg);
				continue;
			}
			const key = pathKey(path);
			if (!fields[key]?.includes(issue.msg)) (fields[key] ??= []).push(issue.msg);
		}
		this.fieldErrors = Object.fromEntries(Object.entries(fields).map(([k, msgs]) => [k, msgs.join('; ')]));
		this.formErrors = rest;
	}

	/** Кадр `settings`: чужое изменение. Без своих правок — перечитать, с правками — предупредить. */
	onEvent(event: LiveEvent): void {
		if (event.type === 'reset') {
			if (this.changes.length === 0) void this.load();
			return;
		}
		if (event.type !== 'settings' || !this.server) return;
		if (this.saving) {
			// Во время PATCH кадр может быть нашим же сохранением — решается после ответа.
			this.#seenDuringSave = Math.max(this.#seenDuringSave ?? 0, event.data.version);
			return;
		}
		if (event.data.version <= this.server.version) return;
		this.#external(event.data.version);
	}

	#external(version: number): void {
		if (this.changes.length === 0) void this.load();
		else this.conflict = version;
	}

	describe(error: ApiError): string {
		return errorText(error);
	}
}
