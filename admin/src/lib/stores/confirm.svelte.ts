export interface ConfirmRequest {
	title: string;
	/** Пояснение; внешний текст допустим — выводится как текст. */
	body?: string;
	confirmText?: string;
	cancelText?: string;
	/** Третья кнопка между отменой и подтверждением (например «Не сохранять»): ответ `choose` — 'alt'. */
	altText?: string;
	danger?: boolean;
	/** Поле ввода (например причина kill): значение возвращает `prompt`. */
	input?: { label: string; placeholder?: string; maxlength?: number; required?: boolean };
}

const ALT = Symbol('alt');
type Answer = string | null | typeof ALT;

interface Pending extends ConfirmRequest {
	resolve: (value: Answer) => void;
}

/** Окно подтверждения: одно на приложение, открывается промисом. */
export class ConfirmService {
	current = $state<Pending | null>(null);

	/** true — подтверждено. */
	async confirm(req: ConfirmRequest): Promise<boolean> {
		return (await this.prompt(req)) !== null;
	}

	/** Введённое значение (без поля — пустая строка) или null — отмена. */
	async prompt(req: ConfirmRequest): Promise<string | null> {
		const value = await this.#ask(req);
		return value === ALT ? null : value;
	}

	/** Окно с тремя кнопками: 'confirm', 'alt' (третья кнопка) или null — отмена. */
	async choose(req: ConfirmRequest): Promise<'confirm' | 'alt' | null> {
		const value = await this.#ask(req);
		return value === ALT ? 'alt' : value === null ? null : 'confirm';
	}

	#ask(req: ConfirmRequest): Promise<Answer> {
		this.current?.resolve(null);
		return new Promise((resolve) => {
			this.current = { ...req, resolve };
		});
	}

	answer(value: string | null): void {
		this.#settle(value);
	}

	/** Нажата третья кнопка. */
	answerAlt(): void {
		this.#settle(ALT);
	}

	#settle(value: Answer): void {
		const pending = this.current;
		this.current = null;
		pending?.resolve(value);
	}
}

export const dialogs = new ConfirmService();
