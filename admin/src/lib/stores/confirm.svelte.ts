export interface ConfirmRequest {
	title: string;
	/** Пояснение; внешний текст допустим — выводится как текст. */
	body?: string;
	confirmText?: string;
	cancelText?: string;
	danger?: boolean;
	/** Поле ввода (например причина kill): значение возвращает `prompt`. */
	input?: { label: string; placeholder?: string; maxlength?: number; required?: boolean };
}

interface Pending extends ConfirmRequest {
	resolve: (value: string | null) => void;
}

/** Окно подтверждения: одно на приложение, открывается промисом. */
export class ConfirmService {
	current = $state<Pending | null>(null);

	/** true — подтверждено. */
	async confirm(req: ConfirmRequest): Promise<boolean> {
		return (await this.prompt(req)) !== null;
	}

	/** Введённое значение (без поля — пустая строка) или null — отмена. */
	prompt(req: ConfirmRequest): Promise<string | null> {
		this.current?.resolve(null);
		return new Promise((resolve) => {
			this.current = { ...req, resolve };
		});
	}

	answer(value: string | null): void {
		const pending = this.current;
		this.current = null;
		pending?.resolve(value);
	}
}

export const dialogs = new ConfirmService();
