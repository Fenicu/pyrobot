/** Подделка `page` из `$app/state` для тестов маршрутов: параметры и адрес меняет сам тест. */
export const page = $state({
	params: {} as Record<string, string>,
	url: new URL('http://app.invalid/')
});
