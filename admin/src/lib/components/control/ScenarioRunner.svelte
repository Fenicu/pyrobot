<script lang="ts">
	import Play from '@lucide/svelte/icons/play';
	import { call, type Api } from '$lib/api/client';
	import { ApiFailure } from '$lib/api/errors';
	import type { ScenarioInfo } from '$lib/api/types';
	import { newKey } from '$lib/commands';
	import { toasts } from '$lib/stores/toasts.svelte';
	import { pretty } from '$lib/util/text';
	import Pill from '../Pill.svelte';
	import ParamForm from './ParamForm.svelte';

	interface Props {
		api: Api;
		scenario: ScenarioInfo;
		/** Запуск принят: id запуска для списка ручных запусков. */
		onqueued?: (runId: number) => void;
	}
	let { api, scenario, onqueued }: Props = $props();
	let busy = $state(false);
	let error = $state('');
	// Ключ живёт до успешного ответа: повтор той же отправки (сбой сети, 503) — тот же ключ.
	let key: string | null = null;

	async function run(params: Record<string, string | number>) {
		key ??= newKey('run');
		busy = true;
		error = '';
		try {
			const out = await call(
				api.POST('/api/v1/scenarios/{name}/run', {
					params: { path: { name: scenario.name } },
					body: { params, idempotency_key: key }
				})
			);
			key = null;
			toasts.show(`Запуск #${out.scenario_run_id} · ${scenario.name}: ${out.status}`, 'ok');
			onqueued?.(out.scenario_run_id);
		} catch (e) {
			error = e instanceof ApiFailure ? e.message : String(e);
			if (e instanceof ApiFailure && (e.error.kind === 'invalid' || e.error.kind === 'validation')) {
				key = null;
			}
		} finally {
			busy = false;
		}
	}
</script>

<section class="card" aria-labelledby="runner-title">
	<div class="mb-2 flex flex-wrap items-center gap-2">
		<h2 id="runner-title" class="font-mono text-sm font-semibold">{scenario.name}</h2>
		{#if scenario.certified}<Pill tone="ok">серт.</Pill>{:else}<Pill>симуляция</Pill>{/if}
	</div>
	{#if !scenario.certified}
		<p class="mb-2 text-xs text-warn-fg">
			Не сертифицирован: исполняется как симуляция, шаги кроме навигации не уходят в игру.
		</p>
	{/if}
	{#if Object.keys(scenario.params).length > 0}
		<p class="mb-2 text-xs text-fg-muted">
			Параметры реестра: <span class="ext-text font-mono">{pretty(scenario.params)}</span>
		</p>
	{/if}
	<ParamForm
		required={scenario.required}
		{busy}
		submitLabel="Запустить"
		onsubmit={run}
		onedit={() => (key = null)}
	/>
	{#if error}<p class="ext-text mt-2 text-sm text-bad-fg" role="alert">{error}</p>{/if}
	<p class="mt-2 flex items-center gap-1 text-xs text-fg-faint">
		<Play class="size-3" aria-hidden="true" /> ключ идемпотентности создаётся сам — двойной клик не запустит
		дважды
	</p>
</section>
