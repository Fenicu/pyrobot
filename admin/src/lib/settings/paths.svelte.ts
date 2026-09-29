const KEY = 'pyrobot.settings.paths';

function saved(): boolean {
	try {
		return localStorage.getItem(KEY) === '1';
	} catch {
		return false;
	}
}

/** Пути настроек (`engine.mode`) — для разработчика: по умолчанию скрыты, выбор хранится в
 * localStorage (не секрет, недоступен — просто не запоминается). */
export class SettingPaths {
	show = $state(saved());

	set(show: boolean): void {
		this.show = show;
		try {
			localStorage.setItem(KEY, show ? '1' : '0');
		} catch {
			// приватный режим или запрет хранилища — выбор живёт до перезагрузки
		}
	}
}

export const settingPaths = new SettingPaths();
