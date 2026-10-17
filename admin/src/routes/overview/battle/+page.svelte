<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/app.svelte';
	import { errorText } from '$lib/api/errors';
	import BattleCard from '$lib/components/overview/BattleCard.svelte';
	import BattleSummary from '$lib/components/overview/BattleSummary.svelte';
	import { BattleStore } from '$lib/overview/battle.svelte';

	const store = new BattleStore(api);
	const hq = $derived(store.data);
	const empty = $derived(hq !== null && (hq.battles[0]?.accounts.length ?? 0) === 0);

	// Опрос живёт, пока открыт экран.
	onMount(() => {
		store.start();
		return () => store.stop();
	});
</script>

<svelte:head><title>Штаб битвы · pyrobot</title></svelte:head>

<div class="space-y-3.5">
	{#if hq === null}
		{#if store.error}
			<p class="card text-sm text-bad-fg" role="alert">Штаб недоступен: {errorText(store.error)}.</p>
		{:else}
			<p class="card text-sm text-fg-muted">Загрузка…</p>
		{/if}
	{:else}
		{#if store.error}
			<p class="text-xs text-bad-fg" role="status">Не удалось обновить: {errorText(store.error)}. Показаны прежние данные.</p>
		{/if}
		{#if empty}
			<section class="card space-y-2 text-sm" aria-label="Нет аккаунтов">
				<p>
					{hq.skipped.length > 0 ? 'Нет включённых аккаунтов: приказывать некому.' : 'Аккаунтов пока нет: приказывать некому.'}
				</p>
				<a href="/accounts" class="btn btn-primary">Открыть аккаунты</a>
			</section>
		{:else}
			<p class="text-xs text-fg-muted">
				Приказ действует на одну битву и перекрывает настройки аккаунта; после битвы аккаунты возвращаются к ним.
				{#if hq.source === 'schedule'}Время битв взято из расписания (13:00 и 22:00 МСК): в игре оно пока не замечено.{/if}
			</p>
			<div class="grid items-start gap-3.5 xl:grid-cols-2">
				{#each hq.battles as battle, i (battle.battle_at)}
					<div class="min-w-0 space-y-3.5">
						<BattleCard
							{battle}
							nearest={i === 0}
							targets={hq.targets}
							companies={hq.companies}
							saving={store.saving}
							result={store.result}
							onsave={(at, body) => void store.save(at, body)}
						/>
						<BattleSummary {battle} nearest={i === 0} />
					</div>
				{/each}
			</div>
			{#if hq.skipped.length > 0}
				<p class="text-xs text-fg-muted">
					Не входят в штаб (аккаунт не включён): {hq.skipped.map((s) => `#${s.account_id}`).join(', ')}.
				</p>
			{/if}
		{/if}
	{/if}
</div>
