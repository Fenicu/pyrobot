import { createSubscriber } from 'svelte/reactivity';

export const TICK_MS = 60_000;

/** «Сейчас» для экранов. Чтение в шаблоне, `$derived` или `$effect` подписывает на тик раз в
 * минуту (таймер идёт, пока есть читатели): после полуночи по МСК «сегодня» и формат дат
 * пересчитываются. Вне эффектов — просто текущий момент. */
export class Clock {
	#subscribe: () => void;

	constructor(periodMs = TICK_MS) {
		this.#subscribe = createSubscriber((update) => {
			const timer = setInterval(update, periodMs);
			return () => clearInterval(timer);
		});
	}

	get now(): Date {
		this.#subscribe();
		return new Date();
	}
}

export const clock = new Clock();
