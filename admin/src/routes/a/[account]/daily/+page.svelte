<script lang="ts">
	import { onMount } from 'svelte';
	import { current } from '$lib/app.svelte';
	import DailyView from '$lib/components/daily/DailyView.svelte';
	import { DailyStore } from '$lib/daily/store.svelte';

	const MAX_DAYS = 30;
	const { api, live } = current.get();
	// «до 14:40» у сегодня — раз в 30 с.
	let now = $state(new Date());
	const daily = new DailyStore(api, MAX_DAYS);

	onMount(() => {
		const t = setInterval(() => (now = new Date()), 30_000);
		daily.start();
		const off = live.subscribe((e) => daily.onEvent(e));
		return () => {
			clearInterval(t);
			off();
			daily.stop();
		};
	});
</script>

<svelte:head><title>Итоги · pyrobot</title></svelte:head>

<h1 class="mb-1 text-lg font-semibold">Итоги</h1>
<p class="mb-3 text-sm text-fg-muted">
	Изменение за день — чистая разница по наблюдаемому балансу за сутки МСК (траты вычтены, пассивный
	доход стартапа учтён). Разовое и потери — объясняющие события из журнала прихода: их суммы уже в
	изменении. Среднее — по полным дням с данными за последние 7 дней. Клик по дню — его разбор.
</p>
<DailyView data={daily.data} error={daily.error} {now} loadedAt={daily.loadedAt} />
