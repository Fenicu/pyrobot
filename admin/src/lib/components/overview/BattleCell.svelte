<script lang="ts">
	import { battleMismatch, type OverviewRow } from '$lib/overview/table';
	import { noteText } from '$lib/overview/battle';

	interface Props {
		row: OverviewRow;
	}
	let { row }: Props = $props();

	const battle = $derived(row.extra?.battle ?? null);
	const mismatch = $derived(battleMismatch(row));
	const READY: Record<string, { mark: string; text: string; tone: string }> = {
		true: { mark: '✓', text: 'выставлена', tone: 'text-ok-fg' },
		false: { mark: '✗', text: 'не выставлена', tone: 'text-bad-fg' },
		null: { mark: '—', text: 'неизвестно', tone: 'text-fg-faint' }
	};
	const ready = $derived(battle ? READY[String(battle.ready)]! : null);
</script>

{#if battle && ready}
	<span data-testid="battle" data-mismatch={mismatch} class="block min-w-0">
		<span class="whitespace-nowrap">{battle.desired}</span>
		<span class="{ready.tone} text-xs" title={ready.text}>
			<span aria-hidden="true">{ready.mark}</span><span class="sr-only">{ready.text}</span>
		</span>
		{#if mismatch}
			<span class="block text-xs text-warn-fg">в игре {battle.in_game}</span>
		{/if}
		{#if !battle.applies && battle.note}
			<span class="block text-xs text-warn-fg">{noteText(battle.note)}</span>
		{/if}
	</span>
{:else}
	<span class="text-fg-faint">—</span>
{/if}
