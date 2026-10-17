<script lang="ts">
	import type { AccountSchedule } from '$lib/api/types';
	import {
		accountMarks,
		itemText,
		segments,
		type Tick
	} from '$lib/overview/schedule';
	import { fmtTime } from '$lib/util/format';

	interface Props {
		account: AccountSchedule;
		name: string;
		now: string | Date;
		hours: number;
		ticks: Tick[];
	}

	let { account, name, now, hours, ticks }: Props = $props();

	const segs = $derived(segments(account.items, now, hours));
	const marks = $derived(accountMarks(account.items, now, hours));
</script>

<div class="group flex items-stretch border-b border-line-soft last:border-b-0 hover:bg-surface-2/30" role="row">
	<!-- Колонка аккаунта (закреплена слева при горизонтальной прокрутке) -->
	<div
		class="sticky left-0 z-10 flex w-44 shrink-0 flex-col justify-center border-r border-line bg-surface px-3 py-2 group-hover:bg-surface-2/30"
	>
		<a href="/a/{account.id}" class="truncate text-sm font-medium text-fg hover:text-accent">
			{name}
		</a>
		{#if account.stale && account.seen_at}
			<span class="truncate text-[11px] text-fg-muted">снимок от {fmtTime(account.seen_at)}</span>
		{:else if account.stale}
			<span class="truncate text-[11px] text-fg-muted">нет снимка</span>
		{/if}
	</div>

	<!-- Шкала времени аккаунта -->
	<div class="relative h-11 flex-1 overflow-visible">
		<!-- Сетка часов -->
		{#each ticks as tick (tick.pos)}
			<div class="pointer-events-none absolute top-0 bottom-0 w-px bg-line-soft/60" style="left: {tick.pos * 100}%"></div>
		{/each}

		<!-- Отрезки занятости и сна -->
		{#each segs as seg (seg.at + seg.kind)}
			{@const info = itemText(seg)}
			<div
				class="absolute top-1.5 bottom-1.5 flex cursor-default items-center overflow-hidden rounded-xs px-1.5 text-xs font-medium shadow-2xs transition-opacity hover:opacity-90 {seg.kind === 'sleep' ? 'border border-msg-fg/30 bg-msg-bg text-msg-fg' : 'border border-accent/30 bg-accent-soft text-accent'}"
				style="left: {seg.start * 100}%; width: {Math.max(0.6, (seg.end - seg.start) * 100)}%"
				title="{info.text} ({fmtTime(seg.at)} — {fmtTime(seg.until)})"
			>
				<span class="truncate">{info.icon} {info.text}</span>
			</div>
		{/each}

		<!-- Точечные метки -->
		{#each marks as m (m.at + m.kind)}
			{@const info = itemText(m)}
			<div
				class="absolute top-1/2 z-5 -translate-x-1/2 -translate-y-1/2 cursor-default"
				style="left: {m.pos * 100}%"
				title="{info.text} ({fmtTime(m.at)})"
			>
				<span
					class="flex h-6 w-6 items-center justify-center rounded-full text-xs shadow-xs transition-transform hover:scale-110 {m.ready ? 'bg-ok-bg ring-1 ring-ok text-ok-fg' : 'border border-line bg-surface text-fg'}"
				>
					{info.icon}
				</span>
			</div>
		{/each}
	</div>
</div>
