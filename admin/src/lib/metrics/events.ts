import { scenarioText } from '$lib/plan/text';

/** Удачный запуск, который двигает метрики (`MetricsOut.events`). */
export interface MetricEvent {
	at: string;
	scenario: string;
}

/** Метка на графике: x — секунды Unix, значок и подпись — как в плане бота. */
export interface Marker {
	x: number;
	icon: string;
	label: string;
}

/** «📈 слив налички в акции» → значок и подпись. */
function split(scenario: string): { icon: string; label: string } {
	const text = scenarioText(scenario);
	const [icon, ...rest] = text.split(' ');
	return rest.length ? { icon: icon!, label: rest.join(' ') } : { icon: '•', label: text };
}

export function markers(events: MetricEvent[]): Marker[] {
	return events.flatMap((e) => {
		const x = new Date(e.at).getTime() / 1000;
		return Number.isNaN(x) ? [] : [{ x, ...split(e.scenario) }];
	});
}

/** Легенда меток окна: виды событий по первому появлению — «📈 слив налички в акции · 🛌 сон». */
export function legend(marks: Marker[]): string {
	const seen = new Map<string, string>();
	for (const m of marks) if (!seen.has(m.icon)) seen.set(m.icon, `${m.icon} ${m.label}`);
	return [...seen.values()].join(' · ');
}
