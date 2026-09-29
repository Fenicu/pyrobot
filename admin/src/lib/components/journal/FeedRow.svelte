<script lang="ts">
	import type { JournalItem } from '$lib/api/types';
	import { fmtMoment } from '$lib/util/format';
	import { ACTION_STATUS, actionCommand, SOURCE, statusTone } from '$lib/util/game';
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
				text: `${SOURCE[item.source] ?? item.source} · ${actionCommand(item.kind, item)}`,
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

<!-- Телефон: время и значок — первой строкой, текст — второй (до двух строк); ПК — одной строкой. -->
<li>
	<button
		type="button"
		class="grid w-full grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-2 gap-y-0.5 border-b border-line-soft px-2 py-2 text-left text-sm hover:bg-surface-2 md:flex {selected
			? 'bg-accent-soft'
			: ''}"
		aria-pressed={selected}
		onclick={() => onselect(item)}
	>
		<time class="shrink-0 font-mono text-xs text-fg-faint md:w-24" datetime={item.at}
			>{fmtMoment(item.at, now, true)}</time
		>
		<span class="justify-self-start"><Pill tone={view.tone}>{view.pill}</Pill></span>
		<span class="col-span-2 line-clamp-2 min-w-0 md:line-clamp-none md:flex-1 md:truncate">
			{view.text}{#if view.tail}{' '}<span class="text-fg-muted">→ {view.tail}</span>{/if}
		</span>
	</button>
</li>
