<script lang="ts">
	import { onMount } from 'svelte';
	import { ArtifactStore } from '$lib/artifact/store.svelte';
	import { current } from '$lib/app.svelte';
	import DailyCard from '$lib/components/daily/DailyCard.svelte';
	import ArtifactCard from '$lib/components/home/ArtifactCard.svelte';
	import CharacterCard from '$lib/components/home/CharacterCard.svelte';
	import GadgetsCard from '$lib/components/home/GadgetsCard.svelte';
	import HeaderControls from '$lib/components/home/HeaderControls.svelte';
	import NextCard from '$lib/components/home/NextCard.svelte';
	import NowCard from '$lib/components/home/NowCard.svelte';
	import TodayCard from '$lib/components/home/TodayCard.svelte';
	import Page from '$lib/components/shell/Page.svelte';
	import { DailyStore } from '$lib/daily/store.svelte';
	import { GadgetsStore } from '$lib/gadgets/store.svelte';
	import { MetroLiveStore } from '$lib/metro/store.svelte';
	import { PlanStore } from '$lib/plan/store.svelte';

	const { id: account, api, live, engine, character } = current.get();

	// Относительное время («через 38 мин») обновляется раз в 30 с.
	let now = $state(new Date());
	// План для «Сейчас» и «Дальше по времени» живёт, пока открыта главная: уход — отмена запроса и таймеров.
	const plan = new PlanStore(api);
	// «Итоги дня»: сегодня и 7 полных суток — по ним темп опыта в «Персонаже».
	const daily = new DailyStore(api, 8);
	// «Сбор артефакта» — тоже только пока открыта главная.
	const artifact = new ArtifactStore(api);
	// «Гаджеты»: план покупки, задача заточки и её ход.
	const gadgets = new GadgetsStore(api);
	// Живой кадр забега метро — в блоке «Сейчас», пока открыта главная.
	const metro = new MetroLiveStore(api);
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

<!-- Временная статичная сетка: блоки по раскладке по умолчанию; в разметке — порядок одной
     колонки телефона. -->
<Page title="Главная" {actions}>
	{#if character.error && !character.loaded}
		<p class="card mb-3.5 text-sm text-bad-fg" role="alert">Состояние недоступно: движок не отвечает.</p>
	{/if}
	<div class="grid items-start gap-3.5 md:grid-cols-2 xl:grid-cols-[5fr_4fr_3fr]">
		<div class="flex min-w-0 flex-col gap-3.5">
			<NowCard plan={plan.outlook} error={plan.error} state={character.state} {now} {account} {metro} />
			<NextCard plan={plan.outlook} error={plan.error} {now} />
		</div>
		<div class="flex min-w-0 flex-col gap-3.5">
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
		<div class="grid min-w-0 items-start gap-3.5 md:col-span-2 md:grid-cols-2 xl:col-span-1 xl:grid-cols-1">
			<TodayCard state={character.state} stale={character.stale} {now} />
			<DailyCard
				day={daily.data?.days[0] ?? null}
				ledgerSince={daily.data?.ledger_since ?? null}
				error={daily.error}
				{now}
				loadedAt={daily.loadedAt}
			/>
			<ArtifactCard {api} artifact={artifact.data} error={artifact.error} {now} onchange={(out) => artifact.set(out)} />
		</div>
	</div>
</Page>
