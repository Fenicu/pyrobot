import { settingLabel } from './labels';
import { leaves, pathKey, type Field, type Section } from './schema';

export interface SettingName {
	/** Подпись раздела: «Сон». */
	section: string;
	/** Подпись настройки: «Длительность сна, ч». */
	label: string;
	/** Поле формы; null — пути нет в схеме (например, настройку убрали). */
	field: Field | null;
}

/** Подписи настроек по пути (`sleep.duration_h`) для панели изменений и истории: раздел и название
 * вместо пути; неизвестный путь — как есть. */
export function settingNames(sections: Section[]): (key: string) => SettingName {
	const index = new Map<string, { section: Section; field: Field }>();
	for (const section of sections) {
		for (const field of leaves(section.fields)) index.set(pathKey(field.path), { section, field });
	}
	return (key) => {
		const hit = index.get(key);
		if (!hit) return { section: '', label: key, field: null };
		return {
			section: settingLabel(hit.section.name, hit.section.title),
			label: settingLabel(key, hit.field.title),
			field: hit.field
		};
	};
}
