<script lang="ts">
	import type { OverviewTotals } from '$lib/api/types';
	import { incomeText, lossText, moneySigned } from '$lib/daily/text';
	import { formatDelta, levelsText, METRICS } from '$lib/overview/totals';
	import { fmtNum } from '$lib/util/format';
	import Card from '../ui/Card.svelte';

	interface Props {
		data: OverviewTotals;
	}
	let { data }: Props = $props();

	const total = $derived(data.total);
	const count = $derived(data.accounts.length);
	const items = $derived(Object.entries(total.items));
	const lost = $derived(-total.losses.reduce((a, k) => a + (k.amounts.money ?? 0), 0));
	const tone = (d: number | null) => (d === null ? 'text-fg-faint' : d < 0 ? 'text-bad-fg' : d > 0 ? 'text-ok-fg' : 'text-fg-muted');
</script>

<Card title="Всего">
	<ul class="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[15px] sm:grid-cols-3 lg:grid-cols-6" aria-label="Изменение за период">
		{#each METRICS as m (m.key)}
			{@const d = total.balance[m.key]!}
			<li title={m.title} class="min-w-0">
				<span aria-hidden="true">{m.icon}</span><span class="sr-only">{m.title}</span>
				<b class="tabular-nums {tone(d.delta)}">{formatDelta(d, m.key, count)}</b>
			</li>
		{/each}
	</ul>
	<dl class="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-sm">
		<div aria-label="Повышения уровня">
			<dt class="inline text-fg-muted">🎚 Повышения уровня</dt>
			<dd class="inline font-medium tabular-nums">{levelsText(total.levels)}</dd>
		</div>
		<div>
			<dt class="inline text-fg-muted">🏆 за задания</dt>
			<dd class="inline font-medium tabular-nums">{fmtNum(total.trophies)}</dd>
		</div>
		{#if lost > 0}
			<div>
				<dt class="inline text-fg-muted">Траты и потери</dt>
				<dd class="inline font-medium text-bad-fg tabular-nums">{moneySigned(-lost)}</dd>
			</div>
		{/if}
	</dl>
	{#if items.length}
		<h3 class="label mt-2.5 mb-1">Предметы для крафта</h3>
		<ul class="flex flex-wrap gap-1" aria-label="Предметы для крафта">
			{#each items as [name, n] (name)}
				<li class="pill pill-muted">{name} ×{n}</li>
			{/each}
		</ul>
	{/if}
	{#if total.income.length}
		<h3 class="label mt-2.5 mb-1">Разовое</h3>
		<ul class="flex flex-wrap gap-1" aria-label="Разовое">
			{#each total.income as k (k.kind)}
				<li class="pill pill-muted whitespace-normal">{incomeText(k)}</li>
			{/each}
		</ul>
	{/if}
	{#if total.losses.length}
		<h3 class="label mt-2.5 mb-1">Потери и траты</h3>
		<ul class="flex flex-wrap gap-1" aria-label="Потери и траты">
			{#each total.losses as k (k.kind)}
				<li class="pill pill-bad whitespace-normal">{lossText(k)}</li>
			{/each}
		</ul>
	{/if}
</Card>
