<script lang="ts">
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import type { JournalItem } from '$lib/api/types';
	import { runCounts, runOutcome, runReply, type RunGroup } from '$lib/journal/chronicle';
	import { scenarioText } from '$lib/plan/text';
	import { keyOf } from '$lib/stores/journal.svelte';
	import { fmtMoment } from '$lib/util/format';
	import Pill from '../Pill.svelte';
	import FeedRow from './FeedRow.svelte';

	interface Props {
		group: RunGroup;
		now: Date;
		expanded: boolean;
		/** Ключ выбранной записи ленты: её строка внутри запуска подсвечена. */
		selected: string | null;
		ontoggle: () => void;
		onselect: (item: JournalItem) => void;
	}
	let { group, now, expanded, selected, ontoggle, onselect }: Props = $props();

	const title = $derived(group.decision?.scenario ? scenarioText(group.decision.scenario) : 'запуск сценария');
	const outcome = $derived(runOutcome(group));
	const reply = $derived(runReply(group));
</script>

<li>
	<!-- Телефон: время, значок, счётчики — первой строкой, сценарий и ответ — второй; ПК — одной. -->
	<button
		type="button"
		class="grid w-full grid-cols-[auto_auto_minmax(0,1fr)_auto] items-baseline gap-x-2 gap-y-0.5 border-b border-line-soft px-2 py-2 text-left text-sm hover:bg-surface-2 md:flex"
		aria-expanded={expanded}
		onclick={ontoggle}
	>
		<time class="shrink-0 font-mono text-xs text-fg-faint md:w-24" datetime={group.started}
			>{fmtMoment(group.started, now, true)}</time
		>
		<Pill tone={outcome.tone === 'ok' ? 'dec' : outcome.tone}>{outcome.tone === 'ok' ? 'запуск' : outcome.text}</Pill>
		<span class="order-last col-span-4 line-clamp-2 min-w-0 md:order-none md:line-clamp-none md:flex-1 md:truncate">
			<span class="font-medium">{title}</span>{#if reply}{' '}<span class="text-fg-muted">— {reply}</span>{/if}
		</span>
		<span class="shrink-0 justify-self-end text-xs text-fg-faint">{runCounts(group)}</span>
		<ChevronRight
			class="size-4 shrink-0 self-center text-fg-faint transition-transform {expanded ? 'rotate-90' : ''}"
			aria-hidden="true"
		/>
	</button>
	{#if expanded}
		<ul class="ml-3 border-l-2 border-accent-soft" aria-label="Шаги: {title}">
			{#each group.items as item (keyOf(item))}
				<FeedRow {item} {now} selected={selected === keyOf(item)} {onselect} />
			{/each}
		</ul>
	{/if}
</li>
