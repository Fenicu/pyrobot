<script lang="ts">
	import { tick } from 'svelte';
	import type { AccountApi } from '$lib/api/account';
	import { call, type Api } from '$lib/api/client';
	import { ApiFailure } from '$lib/api/errors';
	import type { ScenarioInfo } from '$lib/api/types';
	import type { Confirmer } from '$lib/commands';
	import type { LiveEvent } from '$lib/live/sse';
	import ManualCommand from './ManualCommand.svelte';
	import RecentRuns from './RecentRuns.svelte';
	import ScenarioCatalog from './ScenarioCatalog.svelte';
	import ScenarioRunner from './ScenarioRunner.svelte';

	interface Props {
		api: AccountApi;
		/** Глобальный клиент: каталог сценариев общий для аккаунтов. */
		globalApi: Api;
		subscribe?: (handler: (e: LiveEvent) => void) => () => void;
		confirmer?: Confirmer;
		now?: Date;
	}
	let { api, globalApi, subscribe, confirmer, now }: Props = $props();
	let scenarios = $state<ScenarioInfo[]>([]);
	let selected = $state<string | null>(null);
	let error = $state('');
	let refresh = $state(0);
	const scenario = $derived(scenarios.find((s) => s.name === selected) ?? null);

	// На телефоне форма — под длинным каталогом: после выбора прокрутить к ней.
	async function pick(name: string) {
		selected = name;
		await tick();
		document.getElementById('runner-title')?.scrollIntoView?.({ block: 'nearest' });
	}

	$effect(() => {
		call(globalApi.GET('/api/v1/scenarios'))
			.then((list) => (scenarios = list))
			.catch((e: unknown) => (error = e instanceof ApiFailure ? e.message : String(e)));
	});
</script>

{#if error}<p class="card ext-text mb-3 text-sm text-bad-fg" role="alert">{error}</p>{/if}
<div class="grid gap-3 md:grid-cols-[18rem_minmax(0,1fr)]">
	<ScenarioCatalog {scenarios} {selected} onselect={pick} />
	<div class="space-y-3">
		{#if scenario}
			{#key scenario.name}
				<ScenarioRunner {api} {scenario} onqueued={() => (refresh += 1)} />
			{/key}
		{:else}
			<p class="card text-sm text-fg-muted">Выберите сценарий в каталоге — здесь будет форма запуска.</p>
		{/if}
		<ManualCommand {api} {confirmer} />
		<RecentRuns {api} {subscribe} {refresh} {now} />
	</div>
</div>
