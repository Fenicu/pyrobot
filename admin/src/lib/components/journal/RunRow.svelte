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
	<button
		type="button"
		class="flex w-full items-baseline gap-2 border-b border-line-soft px-2 py-2 text-left text-sm hover:bg-surface-2"
		aria-expanded={expanded}
		onclick={ontoggle}
	>
		<time class="w-16 shrink-0 font-mono text-xs text-fg-faint md:w-24" datetime={group.started}
			>{fmtMoment(group.started, now, true)}</time
		>
		<Pill tone={outcome.tone === 'ok' ? 'dec' : outcome.tone}>{outcome.tone === 'ok' ? 'запуск' : outcome.text}</Pill>
		<span class="min-w-0 flex-1 truncate">
			<span class="font-medium">{title}</span>{#if reply}{' '}<span class="text-fg-muted">— {reply}</span>{/if}
		</span>
		<span class="hidden shrink-0 text-xs text-fg-faint sm:inline">{runCounts(group)}</span>
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
