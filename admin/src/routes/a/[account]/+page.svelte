<script lang="ts">
	import LayoutDashboard from '@lucide/svelte/icons/layout-dashboard';
	import { onMount, type Snippet } from 'svelte';
	import { beforeNavigate, goto } from '$app/navigation';
	import { errorText } from '$lib/api/errors';
	import { ArtifactStore } from '$lib/artifact/store.svelte';
	import { current, homeLayout, session } from '$lib/app.svelte';
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
	import type { BlockId } from '$lib/home/blocks';
	import EditBar from '$lib/home/EditBar.svelte';
	import HomeGrid from '$lib/home/HomeGrid.svelte';
	import { phoneOrder } from '$lib/home/layout';
	import { MetroLiveStore } from '$lib/metro/store.svelte';
	import { PlanStore } from '$lib/plan/store.svelte';
	import { leaveGuard } from '$lib/settings/leave';
	import { dialogs } from '$lib/stores/confirm.svelte';
	import { toasts } from '$lib/stores/toasts.svelte';
	import { media, WIDE } from '$lib/util/media.svelte';

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

	// Сетка из 12 колонок — только на широком экране; уже — одна колонка в порядке раскладки, и
	// «Настроить» там нет.
	const wide = media(WIDE);
	const layout = $derived(homeLayout.draft ?? homeLayout.layout);
	const editing = $derived(wide.current && homeLayout.editing);

	async function saveLayout(): Promise<boolean> {
		const ok = await homeLayout.save();
		if (!ok && homeLayout.error) toasts.show(`Раскладка не сохранена: ${errorText(homeLayout.error)}`, 'error');
		return ok;
	}

	// Несохранённая раскладка: уход — «Сохранить» (с ошибкой — остаёмся), «Не сохранять» или
	// «Остаться». Сессия закончилась — сохранить нельзя, переход на вход не держим.
	beforeNavigate(
		leaveGuard({
			dirty: () => session.status === 'authenticated' && homeLayout.dirty,
			confirm: async () => {
				const choice = await dialogs.choose({
					title: 'Сохранить раскладку главной?',
					body: 'Блоки переставлены, но раскладка не сохранена.',
					confirmText: 'Сохранить',
					altText: 'Не сохранять',
					cancelText: 'Остаться'
				});
				if (choice === 'confirm') return saveLayout();
				if (choice === 'alt') homeLayout.cancel();
				return choice === 'alt';
			},
			go: (url, unload) => (unload ? location.assign(url) : void goto(url))
		})
	);
	const blocks: Record<BlockId, Snippet> = {
		now: nowBlock,
		next: nextBlock,
		character: characterBlock,
		gadgets: gadgetsBlock,
		today: todayBlock,
		daily: dailyBlock,
		artifact: artifactBlock
	};

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
			// Ушли с главной — режим правки закончен (несохранённое уже сохранено или отброшено).
			homeLayout.cancel();
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
	{#if wide.current && !homeLayout.editing}
		<button
			type="button"
			class="btn"
			title="Переставить, скрыть и вернуть блоки главной"
			onclick={() => homeLayout.begin()}
		>
			<LayoutDashboard class="size-4" aria-hidden="true" /> Настроить
		</button>
	{/if}
	<HeaderControls {api} status={engine.status} onchange={() => void engine.load()} />
{/snippet}

{#snippet nowBlock()}
	<NowCard plan={plan.outlook} error={plan.error} state={character.state} {now} {account} {metro} />
{/snippet}
{#snippet nextBlock()}
	<NextCard plan={plan.outlook} error={plan.error} {now} />
{/snippet}
{#snippet characterBlock()}
	<CharacterCard
		state={character.state}
		stale={character.stale}
		{now}
		days={daily.data?.days ?? []}
		{api}
		{settingsVersion}
	/>
{/snippet}
{#snippet gadgetsBlock()}
	<GadgetsCard
		{api}
		state={character.state}
		stale={character.stale}
		gadgets={gadgets.data}
		error={gadgets.error}
		status={engine.status}
		onchange={(out) => gadgets.set(out)}
	/>
{/snippet}
{#snippet todayBlock()}
	<TodayCard state={character.state} stale={character.stale} {now} />
{/snippet}
{#snippet dailyBlock()}
	<DailyCard
		day={daily.data?.days[0] ?? null}
		ledgerSince={daily.data?.ledger_since ?? null}
		error={daily.error}
		{now}
		loadedAt={daily.loadedAt}
	/>
{/snippet}
{#snippet artifactBlock()}
	<ArtifactCard {api} artifact={artifact.data} error={artifact.error} {now} onchange={(out) => artifact.set(out)} />
{/snippet}

<Page title="Главная" {actions}>
	{#if character.error && !character.loaded}
		<p class="card mb-3.5 text-sm text-bad-fg" role="alert">Состояние недоступно: движок не отвечает.</p>
	{/if}
	{#if editing}
		<EditBar store={homeLayout} onsave={() => void saveLayout()} />
	{/if}
	{#if wide.current}
		<HomeGrid
			{layout}
			{editing}
			{blocks}
			onchange={(l) => homeLayout.update(l)}
			onhide={(id) => homeLayout.hide(id)}
		/>
	{:else}
		<div class="flex flex-col gap-3.5">
			{#each phoneOrder(layout) as id (id)}
				{@render blocks[id]()}
			{/each}
		</div>
	{/if}
</Page>
