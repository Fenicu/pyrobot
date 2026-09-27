/** Подделка fetch для тестов: обработчик получает Request и тело, возвращает Response. */
export interface Call {
	method: string;
	url: string;
	headers: Headers;
	body: string;
}

export type Handler = (call: Call) => Response | Promise<Response>;

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
	return new Response(status === 204 ? null : JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json', ...headers }
	});
}

export function mockFetch(handler: Handler) {
	const calls: Call[] = [];
	const fn = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
		const request = input instanceof Request ? input : new Request(String(input), init);
		const call: Call = {
			method: request.method,
			url: new URL(request.url).pathname + new URL(request.url).search,
			headers: request.headers,
			body: request.body ? await request.text() : ''
		};
		calls.push(call);
		return handler(call);
	};
	return Object.assign(fn as typeof fetch, { calls });
}
