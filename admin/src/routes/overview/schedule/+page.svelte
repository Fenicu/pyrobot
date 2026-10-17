<script lang="ts">
	import { onMount } from 'svelte';
	import { accounts, api } from '$lib/app.svelte';
	import { errorText } from '$lib/api/errors';
	import ScheduleTimeline from '$lib/components/overview/ScheduleTimeline.svelte';
	import { ScheduleStore } from '$lib/overview/schedule.svelte';
	import { accountTitle } from '$lib/util/game';

	const store = new ScheduleStore(api);
	const sched = $derived(store.data);
	const names = $derived(
		new Map(accounts.list?.map((a) => [a.id, accountTitle(a) || a.name || `#${a.id}`]) ?? [])
	);

	// Опрос живёт, пока открыт экран
	onMount(() => {
		store.start();
		return () => store.stop();
	});
</script>

<svelte:head><title>Расписание · pyrobot</title></svelte:head>

<div class="space-y-3.5">
	{#if sched === null}
		{#if store.error}
			<p class="card text-sm text-bad-fg" role="alert">Расписание недоступно: {errorText(store.error)}.</p>
		{:else}
			<p class="card text-sm text-fg-muted">Загрузка…</p>
		{/if}
	{:else}
		{#if store.error}
			<p class="text-xs text-bad-fg" role="status">
				Не удалось обновить: {errorText(store.error)}. Показаны прежние данные.
			</p>
		{/if}
		<p class="text-xs text-fg-muted">
			Шкала на 12 часов: отрезки занятости и сна, метки готовности и таймеры планировщика бота. Время московское.
		</p>
		<ScheduleTimeline schedule={sched} accountNames={names} />
	{/if}
</div>
