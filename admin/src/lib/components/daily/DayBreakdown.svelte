<script lang="ts">
	import type { DayOut } from '$lib/api/types';
	import { BALANCE, incomeText, lossText, moneySigned, signed } from '$lib/daily/text';

	interface Props {
		day: DayOut;
		/** День до журнала прихода: разового, потерь и предметов за него нет. */
		beforeLedger?: boolean;
		/** Первый день журнала прихода: «дд.мм». */
		ledgerSince?: string | null;
	}
	let { day, beforeLedger = false, ledgerSince = null }: Props = $props();

	const items = $derived(Object.entries(day.items));
</script>

<ul class="flex flex-wrap gap-x-3.5 gap-y-1 text-[15px]" aria-label="Изменение за день">
	{#each BALANCE as b (b.key)}
		{@const delta = day.balance[b.key]?.delta ?? null}
		<li title={b.title}>
			{b.icon}
			{#if delta === null}
				<span class="text-sm text-fg-faint">нет данных</span>
			{:else}
				<b class="tabular-nums {delta < 0 ? 'text-bad-fg' : delta > 0 ? 'text-ok-fg' : 'text-fg-muted'}">{b.key === 'money' ? moneySigned(delta) : signed(delta)}</b>
			{/if}
		</li>
	{/each}
</ul>
{#if day.level}
	<p class="mt-1.5 text-sm">Уровень {day.level.from} → {day.level.to}</p>
{/if}

{#if beforeLedger}
	<p class="mt-2 text-sm text-fg-faint">Разовое — нет данных{ledgerSince ? ` до ${ledgerSince}` : ''}.</p>
{:else}
	<h3 class="label mt-2.5 mb-1">Предметы для крафта</h3>
	{#if items.length}
		<ul class="flex flex-wrap gap-1" aria-label="Предметы для крафта">
			{#each items as [name, n] (name)}
				<li class="pill pill-muted">{name} ×{n}</li>
			{/each}
		</ul>
	{:else}
		<p class="text-sm text-fg-faint">Предметов для крафта не было</p>
	{/if}
	{#if day.income.length}
		<h3 class="label mt-2.5 mb-1">Разовое</h3>
		<ul class="flex flex-wrap gap-1" aria-label="Разовое">
			{#each day.income as k (k.kind)}
				<li class="pill pill-muted whitespace-normal">{incomeText(k)}</li>
			{/each}
		</ul>
	{/if}
	{#if day.losses.length}
		<h3 class="label mt-2.5 mb-1">Потери и траты</h3>
		<ul class="flex flex-wrap gap-1" aria-label="Потери и траты">
			{#each day.losses as k (k.kind)}
				<li class="pill pill-bad whitespace-normal">{lossText(k)}</li>
			{/each}
		</ul>
	{/if}
{/if}
