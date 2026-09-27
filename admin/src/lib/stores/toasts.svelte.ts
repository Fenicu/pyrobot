export type ToastKind = 'info' | 'ok' | 'warn' | 'error';

export interface Toast {
	id: number;
	kind: ToastKind;
	text: string;
}

/** Всплывающие сообщения: текст выводится только как текст. */
export class Toasts {
	items = $state<Toast[]>([]);
	#next = 1;

	show(text: string, kind: ToastKind = 'info', ttlMs = kind === 'error' ? 8000 : 4000): number {
		const id = this.#next++;
		this.items.push({ id, kind, text });
		if (ttlMs > 0) setTimeout(() => this.dismiss(id), ttlMs);
		return id;
	}

	dismiss(id: number): void {
		this.items = this.items.filter((t) => t.id !== id);
	}
}

export const toasts = new Toasts();
