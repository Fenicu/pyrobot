<script lang="ts">
	import type { JournalItem } from '$lib/api/types';
	import { fmtMoment } from '$lib/util/format';
	import { ACTION_STATUS, SOURCE, statusTone } from '$lib/util/game';
	import { firstLine } from '$lib/util/text';
	import Pill from '../Pill.svelte';

	interface Props {
		item: JournalItem;
		selected: boolean;
		now: Date;
		onselect: (item: JournalItem) => void;
	}
	let { item, selected, now, onselect }: Props = $props();

	const view = $derived.by(() => {
		if (item.type === 'action') {
			const tone = statusTone(item.status);
			const bad = tone === 'bad' || tone === 'warn';
			return {
				pill: bad ? (ACTION_STATUS[item.status] ?? item.status) : 'действие',
				tone: bad ? tone : ('ok' as const),
				text: `${SOURCE[item.source] ?? item.source} · ${item.text ?? item.data ?? '—'}`,
				tail: item.reason || (ACTION_STATUS[item.status] ?? item.status)
			};
		}
		if (item.type === 'decision') {
			return {
				pill: 'решение',
				tone: 'dec' as const,
				text: item.kind === 'act' ? (item.scenario ?? '—') : 'ожидание',
				tail: item.until && item.kind !== 'act' ? `${item.reason} до ${fmtMoment(item.until, now)}` : item.reason
			};
		}
		return {
			pill: item.outgoing ? 'моё' : item.kind === 'edit' ? 'правка' : 'игра',
			tone: 'msg' as const,
			text: firstLine(item.text) || '(без текста)',
			tail: ''
		};
	});
</script>

<li>
	<button
		type="button"
		class="flex w-full items-baseline gap-2 border-b border-line-soft px-2 py-2 text-left text-sm hover:bg-surface-2 {selected
			? 'bg-accent-soft'
			: ''}"
		aria-pressed={selected}
		onclick={() => onselect(item)}
	>
		<time class="w-16 shrink-0 font-mono text-xs text-fg-faint md:w-24" datetime={item.at}
			>{fmtMoment(item.at, now, true)}</time
		>
		<Pill tone={view.tone}>{view.pill}</Pill>
		<span class="min-w-0 flex-1 truncate">
			{view.text}{#if view.tail}{' '}<span class="text-fg-muted">→ {view.tail}</span>{/if}
		</span>
	</button>
</li>
