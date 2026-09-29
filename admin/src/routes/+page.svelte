<script lang="ts">
	import { onMount } from 'svelte';
	import { api, character, engine, live } from '$lib/app.svelte';
	import DailyCard from '$lib/components/daily/DailyCard.svelte';
	import CharacterCard from '$lib/components/home/CharacterCard.svelte';
	import ControlsCard from '$lib/components/home/ControlsCard.svelte';
	import PlanCard from '$lib/components/home/PlanCard.svelte';
	import StatusHeader from '$lib/components/home/StatusHeader.svelte';
	import TodayCard from '$lib/components/home/TodayCard.svelte';
	import { DailyStore } from '$lib/daily/store.svelte';
	import { PlanStore } from '$lib/plan/store.svelte';

	// Относительное время («через 38 мин») обновляется раз в 30 с.
	let now = $state(new Date());
	// «План бота» живёт, пока открыта главная: уход — отмена запроса и таймеров.
	const plan = new PlanStore(api);
	// «Итоги дня» — только сегодня.
	const daily = new DailyStore(api, 1);

	// Готовность цикла (tg_offline, spending_blocked, lock_lost, pipeline_unhealthy) не шлёт своего
	// кадра потока — её доходит только опрос статуса движка (раз в 15 с). Пауза и kill уже приходят
	// сразу кадром settings, поэтому здесь — только остальные поля `_planner_ready()`.
	let lastReady: string | null = null;
	$effect(() => {
		const status = engine.status;
		const ready = status
			? `${status.lock_ok}|${status.pipeline_healthy}|${status.spending_blocked ?? ''}|${status.tg.state}`
			: null;
		if (lastReady !== null && ready !== null && ready !== lastReady) plan.readyChanged();
		lastReady = ready;
	});

	onMount(() => {
		const t = setInterval(() => (now = new Date()), 30_000);
		plan.start();
		daily.start();
		const off = live.subscribe((e) => {
			plan.onEvent(e);
			daily.onEvent(e);
		});
		return () => {
			clearInterval(t);
			off();
			plan.stop();
			daily.stop();
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
	<DailyCard
		day={daily.data?.days[0] ?? null}
		ledgerSince={daily.data?.ledger_since ?? null}
		error={daily.error}
		{now}
		loadedAt={daily.loadedAt}
	/>
	<ControlsCard {api} status={engine.status} onchange={() => void engine.load()} />
</div>
