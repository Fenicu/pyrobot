import type { ParamSpec } from '$lib/api/types';

export type ParamValues = Record<string, string>;

/** Проверка значения поля по ParamSpec (сервер проверяет так же — иначе 422). */
export function paramError(spec: ParamSpec, raw: string): string | null {
	const value = raw.trim();
	if (value === '') return 'обязательное поле';
	switch (spec.type) {
		case 'enum':
			return spec.values?.includes(value) ? null : 'нет в списке';
		case 'int': {
			if (!/^-?\d+$/.test(value)) return 'нужно целое число';
			const n = Number(value);
			if (spec.min !== undefined && n < spec.min) return `не меньше ${spec.min}`;
			if (spec.max !== undefined && n > spec.max) return `не больше ${spec.max}`;
			return null;
		}
		case 'string': {
			if (!spec.pattern) return null;
			try {
				return new RegExp(spec.pattern, 'u').test(value) ? null : `не по шаблону ${spec.pattern}`;
			} catch {
				return null;
			}
		}
	}
	return null;
}

/** Значения формы в параметры запуска: int — числом. */
export function paramsOf(required: Record<string, ParamSpec>, values: ParamValues): Record<string, string | number> {
	const out: Record<string, string | number> = {};
	for (const [name, spec] of Object.entries(required)) {
		const v = (values[name] ?? '').trim();
		out[name] = spec.type === 'int' ? Number(v) : v;
	}
	return out;
}
