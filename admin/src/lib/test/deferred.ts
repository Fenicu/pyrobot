/** Отложенный промис: тест сам решает, когда и чем он завершится. */
export interface Deferred<T> {
	promise: Promise<T>;
	resolve: (value: T) => void;
	reject: (reason: unknown) => void;
}

export function deferred<T>(): Deferred<T> {
	let resolve!: (value: T) => void;
	let reject!: (reason: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

/** Дать отработать промисам и микрозадачам. */
export const flush = () => new Promise((r) => setTimeout(r, 0));
