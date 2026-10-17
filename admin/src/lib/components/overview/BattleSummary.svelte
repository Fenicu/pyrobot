<script lang="ts">
	import type { HqBattle } from '$lib/api/types';
	import { noteText, summaryRows } from '$lib/overview/battle';
	import { fmtNum } from '$lib/util/format';
	import { accountTitle, SKILL_MARK } from '$lib/util/game';

	interface Props {
		battle: HqBattle;
		/** Ближайшая битва: только у неё известно, выставлена ли цель в игре. */
		nearest: boolean;
	}
	let { battle, nearest }: Props = $props();

	const rows = $derived(summaryRows(battle));
	const byId = $derived(new Map(battle.accounts.map((a) => [a.id, a])));
	const SKILLS = ['practice', 'theory', 'cunning', 'wisdom'] as const;
	const READY: Record<string, { mark: string; text: string; tone: string }> = {
		true: { mark: '✓', text: 'выставлена', tone: 'text-ok-fg' },
		false: { mark: '✗', text: 'не выставлена', tone: 'text-bad-fg' },
		null: { mark: '—', text: 'неизвестно', tone: 'text-fg-faint' }
	};
</script>

{#if rows.length > 0}
	<div class="grid gap-3 sm:grid-cols-2" aria-label="Сводка по целям">
		{#each rows as row (row.target ?? '')}
			<div class="card" role="group" aria-label={row.label}>
				<div class="mb-1 flex items-baseline justify-between gap-2">
					<h3 class="text-sm font-semibold">{row.label}</h3>
					<span class="text-xs text-fg-muted tabular-nums">{row.count} акк.</span>
				</div>
				<p class="text-xs text-fg-muted tabular-nums">
					{#if row.count - row.unknown > 0}
						<span>🎚 {fmtNum(row.levelSum)} · ср. {row.levelAvg}</span>
					{/if}
					{#if row.unknown > 0}<span>уровень неизвестен: {row.unknown}</span>{/if}
				</p>
				<p class="text-xs text-fg-muted tabular-nums">
					{SKILLS.map((k) => `${SKILL_MARK[k]}${row.skills[k]}`).join(' ')}
				</p>
				{#if nearest && row.target !== null}
					<p class="text-xs text-fg-muted">выставлено {row.ready} из {row.count}</p>
				{/if}
				<ul class="mt-2 space-y-1 border-t border-line-soft pt-2 text-sm">
					{#each row.accountIds as id (id)}
						{@const a = byId.get(id)}
						{#if a}
							{@const ready = READY[String(nearest ? a.ready : null)]!}
							<li class="flex flex-wrap items-baseline gap-x-2">
								<span class="ext-text min-w-0">{accountTitle({ ...a, team_tag: null })}</span>
								<span class="text-fg-muted tabular-nums">{a.level ?? '—'}</span>
								<span class="{ready.tone} text-xs"><span aria-hidden="true">{ready.mark}</span> {ready.text}</span>
								{#if a.note}<span class="text-xs {a.applies ? 'text-fg-muted' : 'text-warn-fg'}">{noteText(a.note)}</span>{/if}
							</li>
						{/if}
					{/each}
				</ul>
			</div>
		{/each}
	</div>
{/if}
