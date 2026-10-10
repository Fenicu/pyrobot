import { tick } from 'svelte';
import { moveItem } from './order';

interface Drag {
	from: number;
	over: number;
	dx: number;
	dy: number;
}

/** Перестановка строк списка за ручку: указателем (мышь и палец) и стрелками с клавиатуры.
 * Ручка — кнопка с `data-reorder-handle` = id строки; `items` в `down` — строки в порядке ids. */
export class Reorder {
	drag = $state.raw<Drag | null>(null);
	#ids: () => number[];
	#commit: (ids: number[]) => unknown;
	// Места строк на момент захвата: строки во время перетаскивания сдвинуты transform.
	#rects: DOMRect[] = [];
	#x = 0;
	#y = 0;

	constructor(ids: () => number[], commit: (ids: number[]) => unknown) {
		this.#ids = ids;
		this.#commit = commit;
	}

	down(e: PointerEvent, index: number, items: HTMLElement[]): void {
		if (e.button !== 0 || this.drag !== null) return;
		e.preventDefault();
		(e.currentTarget as Element | null)?.setPointerCapture?.(e.pointerId);
		this.#rects = items.map((el) => el.getBoundingClientRect());
		this.#x = e.clientX;
		this.#y = e.clientY;
		this.drag = { from: index, over: index, dx: 0, dy: 0 };
	}

	move(e: PointerEvent): void {
		const d = this.drag;
		if (d === null) return;
		e.preventDefault();
		const dx = e.clientX - this.#x;
		const dy = e.clientY - this.#y;
		this.drag = { ...d, dx, dy, over: this.#nearest(d.from, dx, dy) };
	}

	up(): void {
		const d = this.drag;
		if (d === null) return;
		this.drag = null;
		if (d.over !== d.from) void this.#commit(moveItem(this.#ids(), d.from, d.over));
	}

	cancel(): void {
		this.drag = null;
	}

	/** Сдвиг строки: перетаскиваемая идёт за указателем, остальные — на освободившееся место. */
	shift(index: number): string | undefined {
		const d = this.drag;
		if (d === null) return undefined;
		if (index === d.from) return `translate(${d.dx}px, ${d.dy}px)`;
		const to = d.from < index && index <= d.over ? index - 1 : d.over <= index && index < d.from ? index + 1 : index;
		const a = this.#rects[index];
		const b = this.#rects[to];
		if (to === index || !a || !b) return undefined;
		return `translate(${b.left - a.left}px, ${b.top - a.top}px)`;
	}

	/** Стрелки вверх и вниз — на одно место; фокус остаётся на ручке переставленной строки
	 * (ищется в `list`: та же строка бывает и в другом списке на экране). */
	async key(e: KeyboardEvent, index: number, list: HTMLElement | undefined): Promise<void> {
		const step = e.key === 'ArrowUp' ? -1 : e.key === 'ArrowDown' ? 1 : 0;
		if (step === 0) return;
		e.preventDefault();
		const ids = this.#ids();
		const to = index + step;
		if (to < 0 || to >= ids.length) return;
		void this.#commit(moveItem(ids, index, to));
		await tick();
		list?.querySelector<HTMLElement>(`[data-reorder-handle="${ids[index]}"]`)?.focus();
	}

	// Новое место — у строки, чей центр ближе всего к центру перетаскиваемой.
	#nearest(from: number, dx: number, dy: number): number {
		const r = this.#rects[from];
		if (!r) return from;
		const x = r.left + r.width / 2 + dx;
		const y = r.top + r.height / 2 + dy;
		let best = from;
		let dist = Infinity;
		this.#rects.forEach((o, i) => {
			const d = (o.left + o.width / 2 - x) ** 2 + (o.top + o.height / 2 - y) ** 2;
			if (d < dist) {
				dist = d;
				best = i;
			}
		});
		return best;
	}
}
