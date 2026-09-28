<script lang="ts">
	import { onMount } from 'svelte';
	import { api, character, engine, live } from '$lib/app.svelte';
	import CharacterCard from '$lib/components/home/CharacterCard.svelte';
	import ControlsCard from '$lib/components/home/ControlsCard.svelte';
	import PlanCard from '$lib/components/home/PlanCard.svelte';
	import StatusHeader from '$lib/components/home/StatusHeader.svelte';
	import TodayCard from '$lib/components/home/TodayCard.svelte';
	import { PlanStore } from '$lib/plan/store.svelte';

	// Относительное время («через 38 мин») обновляется раз в 30 с.
	let now = $state(new Date());
	// «План бота» живёт, пока открыта главная: уход — отмена запроса и таймеров.
	const plan = new PlanStore(api);
	onMount(() => {
		const t = setInterval(() => (now = new Date()), 30_000);
		plan.start();
		const off = live.subscribe((e) => plan.onEvent(e));
		return () => {
			clearInterval(t);
			off();
			plan.stop();
		};
	});
</script>

<svelte:head><title>Главная · pyrobot</title></svelte:head>

<h1 class="sr-only">Главная</h1>
<div class="mx-auto max-w-5xl space-y-3">
	<StatusHeader
		status={engine.status}
		error={engine.error}
		live={live.status}
		retryIn={live.retryIn}
		state={character.state}
		{now}
	/>
	{#if character.error && !character.loaded}
		<p class="card text-sm text-bad-fg" role="alert">Состояние недоступно: движок не отвечает.</p>
	{/if}
	<PlanCard plan={plan.outlook} error={plan.error} state={character.state} {now} />
	<div class="grid gap-3 md:grid-cols-2">
		<CharacterCard state={character.state} stale={character.stale} {now} />
		<TodayCard state={character.state} stale={character.stale} {now} />
	</div>
	<ControlsCard {api} status={engine.status} onchange={() => void engine.load()} />
</div>
