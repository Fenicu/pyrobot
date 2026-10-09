<script lang="ts">
	import { errorText, type ApiError } from '$lib/api/errors';
	import type { Outlook, PlanTimer } from '$lib/api/types';
	import { basisText } from '$lib/plan/now';
	import { timerLine } from '$lib/plan/text';
	import { fmtMoment, fmtRelative, fmtTime } from '$lib/util/format';
	import Card from '../ui/Card.svelte';

	interface Props {
		plan: Outlook | null;
		error: ApiError | null;
		now: Date;
	}
	let { plan, error, now }: Props = $props();

	/** На телефоне сразу видны первые 5 событий, остальное — по «Ещё». */
	const PHONE_TIMERS = 5;
	let more = $state(false);
	const uid = $props.id();
	const timersId = `${uid}-timers`;
	const laterId = `${uid}-later`;

	const stopped = $derived(error?.kind === 'engine_down' && error.code !== 'planner not started');
	const basis = $derived(plan ? basisText(plan) : '');
	const timers = $derived(plan?.wakeups.filter((t) => !t.after_wake) ?? []);
	const later = $derived(plan?.wakeups.filter((t) => t.after_wake) ?? []);
	const hidden = $derived(Math.max(timers.length + later.length - PHONE_TIMERS, 0));
	const phoneOnly = (i: number) => (i >= PHONE_TIMERS && !more ? 'hidden md:grid' : 'grid');
	const controls = $derived(later.length > 0 ? `${timersId} ${laterId}` : timersId);
</script>

{#snippet timerRow(t: PlanTimer, i: number)}
	{@const line = timerLine(t, plan!)}
	<li
		class="{phoneOnly(i)} grid-cols-[3.25rem_1.5rem_minmax(0,1fr)_auto] items-baseline gap-x-2 border-b border-line-soft py-1.5 text-sm last:border-0"
		data-kind={t.kind}
	>
		<span class="font-mono text-fg-muted tabular-nums" title={fmtMoment(t.at, now)}>{fmtTime(t.at)}</span>
		<span aria-hidden="true">{line.icon}</span>
		<span class="min-w-0">
			{line.text}
			{#if line.detail}<span class="block text-xs text-fg-faint">{line.detail}</span>{/if}
		</span>
		<span class="text-xs whitespace-nowrap text-fg-faint">{fmtRelative(t.at, now)}</span>
	</li>
{/snippet}

<Card title={basis ? `Дальше по времени · ${basis}` : 'Дальше по времени'}>
	{#if !plan}
		<!-- Тревога о плане — одна, в «Сейчас»; здесь приглушённо. -->
		{#if stopped}
			<p class="text-sm text-fg-muted">Таймеры недоступны: движок не запущен.</p>
		{:else if error}
			<p class="text-sm text-fg-muted">Таймеры недоступны: {errorText(error)}.</p>
		{:else}
			<p class="text-sm text-fg-muted">Загрузка плана…</p>
		{/if}
	{:else}
		{#if timers.length + later.length === 0}
			<p class="text-sm text-fg-faint">Таймеров нет: бот ждёт событий.</p>
		{/if}
		<ul id={timersId}>
			{#each timers as t, i (t.kind + (t.key ?? ''))}
				{@render timerRow(t, i)}
			{/each}
		</ul>
		{#if later.length > 0}
			<h3 class="mt-2 text-xs text-fg-faint {timers.length >= PHONE_TIMERS && !more ? 'hidden md:block' : ''}">
				после пробуждения
			</h3>
			<ul id={laterId}>
				{#each later as t, i (t.kind + (t.key ?? ''))}
					{@render timerRow(t, timers.length + i)}
				{/each}
			</ul>
		{/if}
		{#if hidden > 0}
			<button
				type="button"
				class="btn mt-2 w-full md:hidden"
				aria-expanded={more}
				aria-controls={controls}
				onclick={() => (more = !more)}
			>
				{more ? 'Свернуть' : 'Ещё'}
			</button>
		{/if}
	{/if}
</Card>
