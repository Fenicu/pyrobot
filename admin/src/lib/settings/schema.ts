/** Форма настроек по JSON Schema из `GET /settings` (pydantic `model_json_schema`). */

export interface JsonSchema {
	type?: string | string[];
	title?: string;
	description?: string;
	default?: unknown;
	enum?: unknown[];
	const?: unknown;
	minimum?: number;
	maximum?: number;
	exclusiveMinimum?: number;
	exclusiveMaximum?: number;
	readOnly?: boolean;
	/** Бот настройку не читает (`UNUSED` в `app/engine/settings.py`). */
	'x-unused'?: boolean;
	items?: JsonSchema;
	prefixItems?: JsonSchema[];
	properties?: Record<string, JsonSchema>;
	additionalProperties?: JsonSchema | boolean;
	anyOf?: JsonSchema[];
	$ref?: string;
	$defs?: Record<string, JsonSchema>;
}

export type Path = string[];
export type Json = unknown;

export type FieldKind =
	| { kind: 'boolean' }
	| { kind: 'enum'; options: string[] }
	| { kind: 'const'; value: unknown }
	| { kind: 'number'; integer: boolean; min?: number; max?: number; exclusiveMin?: boolean; exclusiveMax?: boolean }
	| { kind: 'string' }
	| { kind: 'enum_tags'; options: string[] }
	| { kind: 'string_tags' }
	| { kind: 'max_or_int'; min?: number }
	| { kind: 'nullable'; inner: FieldKind }
	| { kind: 'group'; fields: Field[] }
	| { kind: 'map'; value: FieldKind }
	| { kind: 'json' };

export interface Field {
	name: string;
	path: Path;
	title: string;
	description?: string;
	readOnly: boolean;
	/** Бот её не читает: менять можно, но ни на что не влияет. */
	unused: boolean;
	type: FieldKind;
}

export function resolve(schema: JsonSchema, root: JsonSchema): JsonSchema {
	let current = schema;
	for (let i = 0; i < 10 && current.$ref; i++) {
		const name = current.$ref.replace('#/$defs/', '');
		const target = root.$defs?.[name];
		if (!target) break;
		const { $ref: _, ...rest } = current;
		current = { ...target, ...rest };
	}
	return current;
}

const isNull = (s: JsonSchema) => s.type === 'null';

export function classify(raw: JsonSchema, root: JsonSchema, path: Path): FieldKind {
	const s = resolve(raw, root);
	if (s.anyOf) {
		const options = s.anyOf.map((o) => resolve(o, root));
		const nonNull = options.filter((o) => !isNull(o));
		if (nonNull.length === 1 && options.length === 2) {
			return { kind: 'nullable', inner: classify(nonNull[0]!, root, path) };
		}
		const int = options.find((o) => o.type === 'integer');
		const max = options.find((o) => o.const === 'max');
		if (options.length === 2 && int && max) return { kind: 'max_or_int', min: int.minimum };
		return { kind: 'json' };
	}
	if (s.const !== undefined) return { kind: 'const', value: s.const };
	if (s.type === 'boolean') return { kind: 'boolean' };
	if (s.type === 'string') {
		return s.enum ? { kind: 'enum', options: s.enum.map(String) } : { kind: 'string' };
	}
	if (s.type === 'integer' || s.type === 'number') {
		return {
			kind: 'number',
			integer: s.type === 'integer',
			min: s.minimum ?? s.exclusiveMinimum,
			max: s.maximum ?? s.exclusiveMaximum,
			exclusiveMin: s.exclusiveMinimum !== undefined,
			exclusiveMax: s.exclusiveMaximum !== undefined
		};
	}
	if (s.type === 'array') {
		const items = s.items ? resolve(s.items, root) : undefined;
		if (items?.enum) return { kind: 'enum_tags', options: items.enum.map(String) };
		if (items?.type === 'string') return { kind: 'string_tags' };
		if (s.prefixItems?.every((p) => resolve(p, root).type === 'string')) return { kind: 'string_tags' };
		return { kind: 'json' };
	}
	if (s.type === 'object' || s.properties) {
		if (s.properties) return { kind: 'group', fields: fieldsOf(s, root, path) };
		if (s.additionalProperties && typeof s.additionalProperties === 'object') {
			return { kind: 'map', value: classify(s.additionalProperties, root, path) };
		}
	}
	return { kind: 'json' };
}

export function fieldsOf(schema: JsonSchema, root: JsonSchema, base: Path = []): Field[] {
	const s = resolve(schema, root);
	return Object.entries(s.properties ?? {}).map(([name, raw]) => {
		const prop = resolve(raw, root);
		const path = [...base, name];
		return {
			name,
			path,
			title: prop.title ?? name,
			description: prop.description,
			readOnly: prop.readOnly === true,
			unused: prop['x-unused'] === true,
			type: classify(raw, root, path)
		};
	});
}

export interface Section {
	name: string;
	title: string;
	description?: string;
	fields: Field[];
}

/** Секции — корневые свойства схемы (engine, features, …). */
export function sectionsOf(root: JsonSchema): Section[] {
	return fieldsOf(root, root).map((f) => ({
		name: f.name,
		title: f.title,
		description: resolve((root.properties ?? {})[f.name] ?? {}, root).description,
		fields: f.type.kind === 'group' ? f.type.fields : [f]
	}));
}

export function getAt(obj: Json, path: Path): Json {
	let cur: Json = obj;
	for (const key of path) {
		if (cur === null || typeof cur !== 'object') return undefined;
		cur = (cur as Record<string, Json>)[key];
	}
	return cur;
}

export function setAt(obj: Json, path: Path, value: Json): Json {
	if (path.length === 0) return value;
	const [head, ...rest] = path as [string, ...string[]];
	const base = obj !== null && typeof obj === 'object' ? (obj as Record<string, Json>) : {};
	return { ...base, [head]: setAt(base[head], rest, value) };
}

export const same = (a: Json, b: Json) => JSON.stringify(a) === JSON.stringify(b);

/** Листья формы (всё, кроме групп): списки и словари меняются целиком, как в PATCH. */
export function leaves(fields: Field[]): Field[] {
	return fields.flatMap((f) => (f.type.kind === 'group' ? leaves(f.type.fields) : [f]));
}

/** Поля для формы: без «только чтение» (kill, причина kill, пауза — их меняют кнопки на главной) и
 * без групп, в которых после этого ничего не осталось. */
export function editable(fields: Field[]): Field[] {
	return fields.flatMap((f) => {
		if (f.readOnly) return [];
		if (f.type.kind !== 'group') return [f];
		const inner = editable(f.type.fields);
		return inner.length ? [{ ...f, type: { ...f.type, fields: inner } }] : [];
	});
}

/** Изменённые листья черновика относительно значений сервера. */
export function changedPaths(sections: Section[], base: Json, draft: Json): Path[] {
	return sections
		.flatMap((s) => leaves(s.fields))
		.filter((f) => !f.readOnly && !same(getAt(base, f.path), getAt(draft, f.path)))
		.map((f) => f.path);
}

/** Тело `changes` PATCH: только изменённые листья, по секциям. */
export function buildChanges(draft: Json, paths: Path[]): Record<string, Json> {
	let out: Json = {};
	for (const p of paths) out = setAt(out, p, getAt(draft, p));
	return out as Record<string, Json>;
}

export const pathKey = (p: Path) => p.join('.');
