<script lang="ts">
	import { onMount } from 'svelte';
	import { ArtifactStore } from '$lib/artifact/store.svelte';
	import { current } from '$lib/app.svelte';
	import DailyCard from '$lib/components/daily/DailyCard.svelte';
	import ArtifactCard from '$lib/components/home/ArtifactCard.svelte';
	import CharacterCard from '$lib/components/home/CharacterCard.svelte';
	import GadgetsCard from '$lib/components/home/GadgetsCard.svelte';
	import HeaderControls from '$lib/components/home/HeaderControls.svelte';
	import MetroLiveCard from '$lib/components/home/MetroLiveCard.svelte';
	import PlanCard from '$lib/components/home/PlanCard.svelte';
	import TodayCard from '$lib/components/home/TodayCard.svelte';
	import Page from '$lib/components/shell/Page.svelte';
	import { DailyStore } from '$lib/daily/store.svelte';
	import { GadgetsStore } from '$lib/gadgets/store.svelte';
	import { shown } from '$lib/metro/live';
	import { MetroLiveStore } from '$lib/metro/store.svelte';
	import { PlanStore } from '$lib/plan/store.svelte';

	const { id: account, api, live, engine, character } = current.get();

	// Относительное время («через 38 мин») обновляется раз в 30 с.
	let now = $state(new Date());
	// «План бота» живёт, пока открыта главная: уход — отмена запроса и таймеров.
	const plan = new PlanStore(api);
	// «Итоги дня»: сегодня и 7 полных суток — по ним темп опыта в «Персонаже».
	const daily = new DailyStore(api, 8);
	// «Сбор артефакта» — тоже только пока открыта главная.
	const artifact = new ArtifactStore(api);
	// «Гаджеты при тебе»: план покупки, задача заточки и её ход.
	const gadgets = new GadgetsStore(api);
	// «Метро — прохождение»: живой кадр забега, пока открыта главная.
	const metro = new MetroLiveStore(api);
	const metroShown = $derived(shown(metro.frame, metro.receivedAt, now.getTime()));
	// Версия настроек из потока: сменилась — «Персонаж» перечитывает, кому дарятся 🍊.
	let settingsVersion = $state<number | null>(null);

	// Готовность цикла (tg_offline, spending_blocked, lock_lost, pipeline_unhealthy) не шлёт своего
	// кадра потока — её доходит только опрос статуса движка (раз в 15 с). Пауза и kill уже приходят
	// сразу кадром settings, поэтому здесь — только остальные поля `_planner_ready()`.
	let lastReady: string | null = null;
	$effect(() => {
		const status = engine.status;
		const ready = status
			? `${status.lease_ok}|${status.pipeline_healthy}|${status.spending_blocked ?? ''}|${status.tg.state}`
			: null;
		if (lastReady !== null && ready !== null && ready !== lastReady) plan.readyChanged();
		lastReady = ready;
	});

	onMount(() => {
		const t = setInterval(() => (now = new Date()), 30_000);
		plan.start();
		daily.start();
		artifact.start();
		gadgets.start();
		metro.start();
		const off = live.subscribe((e) => {
			plan.onEvent(e);
			daily.onEvent(e);
			artifact.onEvent(e);
			gadgets.onEvent(e);
			metro.onEvent(e);
			if (e.type === 'settings') settingsVersion = e.data.version;
		});
		return () => {
			clearInterval(t);
			off();
			plan.stop();
			daily.stop();
			artifact.stop();
			gadgets.stop();
			metro.stop();
		};
	});
</script>

{#snippet actions()}
	<HeaderControls {api} status={engine.status} onchange={() => void engine.load()} />
{/snippet}

<Page title="Главная" {actions}>
	<div class="space-y-3">
		{#if character.error && !character.loaded}
			<p class="card text-sm text-bad-fg" role="alert">Состояние недоступно: движок не отвечает.</p>
		{/if}
		<PlanCard
			plan={plan.outlook}
			error={plan.error}
			state={character.state}
			{now}
			metroHref={metroShown ? '#metro-live' : null}
		/>
		<MetroLiveCard
			frame={metro.frame}
			receivedAt={metro.receivedAt}
			{now}
			{account}
			metroRunning={plan.outlook?.loop.current === 'metro'}
			metroRunId={metro.metroRunId}
		/>
		<div class="grid gap-3 md:grid-cols-2">
			<div class="space-y-3">
				<CharacterCard
					state={character.state}
					stale={character.stale}
					{now}
					days={daily.data?.days ?? []}
					{api}
					{settingsVersion}
				/>
				<GadgetsCard
					{api}
					state={character.state}
					stale={character.stale}
					gadgets={gadgets.data}
					error={gadgets.error}
					status={engine.status}
					onchange={(out) => gadgets.set(out)}
				/>
			</div>
			<TodayCard state={character.state} stale={character.stale} {now} />
		</div>
		<ArtifactCard {api} artifact={artifact.data} error={artifact.error} {now} onchange={(out) => artifact.set(out)} />
		<DailyCard
			day={daily.data?.days[0] ?? null}
			ledgerSince={daily.data?.ledger_since ?? null}
			error={daily.error}
			{now}
			loadedAt={daily.loadedAt}
		/>
	</div>
</Page>
