<script lang="ts">
	import { ACTIVITY_COLOR, accountActivity, accountDotLabel, accountTone } from '$lib/accounts/status';
	import { accountHref } from '$lib/nav';
	import type { Selection } from '$lib/overview/bulk';
	import { readyText } from '$lib/plan/text';
	import {
		artifactText,
		metroText,
		nextActionText,
		rowProblems,
		skillsText,
		type OverviewRow
	} from '$lib/overview/table';
	import { fmtNum, fmtTime } from '$lib/util/format';
	import { accountTitle } from '$lib/util/game';
	import Meter from '../Meter.svelte';
	import StatusDot from '../ui/StatusDot.svelte';
	import BattleCell from './BattleCell.svelte';

	interface Props {
		rows: OverviewRow[];
		now: Date;
		/** Нет - флажков нет. */
		selection?: Selection;
	}
	let { rows, now, selection }: Props = $props();

	const pickable = $derived(rows.filter((r) => r.account.status !== 'deleting'));
	const allPicked = $derived(
		selection !== undefined && pickable.length > 0 && pickable.every((r) => selection.selected.has(r.account.id))
	);

	const uid = $props.id();
	let opened = $state<Set<number>>(new Set());

	function toggle(id: number) {
		const next = new Set(opened);
		if (!next.delete(id)) next.add(id);
		opened = next;
	}
</script>

{#if selection}
	<label class="mb-2 flex items-center gap-2 text-sm">
		<input type="checkbox" checked={allPicked} onchange={(e) => selection.toggleAll((e.currentTarget as HTMLInputElement).checked)} />
		Выбрать все
	</label>
{/if}
<ul class="space-y-2.5" aria-label="Аккаунты">
	{#each rows as row (row.account.id)}
		{@const a = row.account}
		{@const x = row.extra}
		{@const act = accountActivity(a, now)}
		{@const problem = rowProblems(row)[0]}
		{@const stale = x?.stale ?? false}
		{@const deleting = a.status === 'deleting'}
		{@const open = opened.has(a.id)}
		{@const next = x?.next_action ? nextActionText(x.next_action) : null}
		<li class="rounded-card border border-line bg-surface p-3 text-sm {deleting ? 'text-fg-faint' : ''}">
			<div class="flex items-center gap-2">
				{#if selection}
					<input
						type="checkbox"
						aria-label="Выбрать {accountTitle(a)}"
						disabled={deleting}
						checked={selection.selected.has(a.id)}
						onchange={() => selection.toggle(a.id)}
					/>
				{/if}
				<StatusDot tone={accountTone(a)} label={accountDotLabel(a)} />
				{#if deleting}
					<span class="ext-text min-w-0 flex-1 font-medium">{accountTitle(a)}</span>
				{:else}
					<a href={accountHref(a.id, '')} class="ext-text min-w-0 flex-1 font-medium">{accountTitle(a)}</a>
				{/if}
				{#if a.level != null}<span class="shrink-0 text-fg-muted">ур. {a.level}</span>{/if}
			</div>
			<p class="mt-1 {ACTIVITY_COLOR[act.tone]}" data-testid="activity">
				{act.text}{#if act.until}<span class="text-fg-muted tabular-nums"> до {fmtTime(act.until)}</span>{/if}
			</p>
			{#if x?.battle}
				<p class="mt-1"><span class="text-fg-muted">Цель: </span><BattleCell {row} /></p>
			{/if}
			{#if problem}<p class="mt-1 text-warn-fg" data-testid="problem">{problem}</p>{/if}
			{#if stale && x?.seen_at}<p class="mt-1 text-xs text-fg-faint">снимок от {fmtTime(x.seen_at)}</p>{/if}

			{#if !deleting && x}
				<button
					type="button"
					class="btn mt-2 w-full"
					aria-expanded={open}
					aria-controls="{uid}-more-{a.id}"
					onclick={() => toggle(a.id)}
				>
					{open ? 'Свернуть' : 'Подробнее'}
				</button>
				{#if open}
					<dl
						id="{uid}-more-{a.id}"
						class="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5 border-t border-line-soft pt-2 text-xs {stale ? 'opacity-70' : ''}"
					>
						<div><dt class="text-fg-muted">💡 опыт</dt><dd>
							{#if x.exp != null && x.exp_next}
								<Meter value={x.exp} max={x.exp_next} label="Опыт аккаунта {a.name}" />
								<span class="tabular-nums">{fmtNum(x.exp)} / {fmtNum(x.exp_next)}</span>
							{:else}—{/if}
						</dd></div>
						<div><dt class="text-fg-muted">💵 деньги</dt><dd class="tabular-nums">{fmtNum(x.money)}</dd></div>
						<div><dt class="text-fg-muted">🔋 энергия</dt><dd class="tabular-nums">{fmtNum(x.stamina)}</dd></div>
						<div><dt class="text-fg-muted">🔥 мотивация</dt><dd class="tabular-nums">{x.motivation != null ? `${x.motivation}/${x.motivation_max ?? '?'}` : '—'}</dd></div>
						<div><dt class="text-fg-muted">📚 знания</dt><dd class="tabular-nums">{fmtNum(x.knowledge)}</dd></div>
						<div><dt class="text-fg-muted">🔩 сырьё</dt><dd class="tabular-nums">{fmtNum(x.raw)}</dd></div>
						<div><dt class="text-fg-muted">⚙️ детали</dt><dd class="tabular-nums">{fmtNum(x.details)}</dd></div>
						<div><dt class="text-fg-muted">навыки</dt><dd class="tabular-nums">{skillsText(x)}</dd></div>
						<div>
							<dt class="text-fg-muted">бот</dt>
							<dd>
								{#if next}{next.text}{#if next.at}<span class="tabular-nums"> в {fmtTime(next.at)}</span>{/if}{:else}—{/if}
								{#if x.next_action?.blocked_by}<span class="block text-warn-fg">{readyText(x.next_action.blocked_by)}</span>{/if}
							</dd>
						</div>
						<div><dt class="text-fg-muted">👾 артефакт</dt><dd>{artifactText(x, now)}</dd></div>
						<div><dt class="text-fg-muted">🚇 метро</dt><dd>{metroText(row, now)}</dd></div>
						<div>
							<dt class="text-fg-muted">непрочитанное</dt>
							<dd>{a.unread.error + a.unread.warn > 0 ? `${a.unread.error} ошибок, ${a.unread.warn} предупр.` : '—'}</dd>
						</div>
					</dl>
				{/if}
			{/if}
		</li>
	{/each}
</ul>
