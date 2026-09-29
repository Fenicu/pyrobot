<script lang="ts">
	import { errorText, type ApiError } from '$lib/api/errors';
	import type { Outlook, PlanCandidate, PlanTimer, PublicState } from '$lib/api/types';
	import { basisText, explain, nowView } from '$lib/plan/now';
	import {
		actDetail,
		candidateDetail,
		scenarioText,
		timerLine,
		verdictText,
		verdictTone
	} from '$lib/plan/text';
	import { fmtMoment, fmtRelative, fmtTime } from '$lib/util/format';
	import Pill from '../Pill.svelte';

	interface Props {
		plan: Outlook | null;
		error: ApiError | null;
		state: PublicState;
		now: Date;
	}
	// Снимок — для строки пояснения (задания дня); `state` занят руной.
	let { plan, error, state: snapshot, now }: Props = $props();

	/** На телефоне сразу видно «Сейчас» и первые 5 событий, остальное — по «Ещё». */
	const PHONE_TIMERS = 5;
	let more = $state(false);

	// Срок, до которого спит цикл, наступает между тиками часов главной (раз в 30 с): «Сейчас»
	// перерисовывается в сам срок.
	let woke = $state<Date | null>(null);
	const clock = $derived(woke !== null && woke.getTime() > now.getTime() ? woke : now);
	$effect(() => {
		const wake = plan?.loop.wake_at;
		if (!wake) return;
		const left = Date.parse(wake) - Date.now();
		if (left <= 0) return;
		const timer = setTimeout(() => (woke = new Date(wake)), left);
		return () => clearTimeout(timer);
	});
	const view = $derived(plan ? nowView(plan, clock) : null);
	// Занятость устарела: кроме решения, план — второй проход по последним известным значениям.
	const basis = $derived(plan ? basisText(plan) : '');
	const why = $derived(plan ? explain(plan, snapshot, now) : '');
	const timers = $derived(plan?.wakeups.filter((t) => !t.after_wake) ?? []);
	const later = $derived(plan?.wakeups.filter((t) => t.after_wake) ?? []);
	const hidden = $derived(
		(plan?.considered.length ?? 0) +
			(plan?.basis?.considered.length ?? 0) +
			(plan?.also_ready.length ?? 0) +
			Math.max(timers.length + later.length - PHONE_TIMERS, 0)
	);
	const phoneOnly = (i: number) => (i >= PHONE_TIMERS && !more ? 'hidden md:grid' : 'grid');
	const extra = $derived(more ? '' : 'hidden md:block');
	// «Ещё» раскрывает несколько панелей и списков разом: id тех, что сейчас есть в DOM.
	const controls = $derived(
		[
			'plan-why-panel',
			(plan?.also_ready.length ?? 0) > 0 ? 'plan-ready-panel' : null,
			'plan-timers-list',
			later.length > 0 ? 'plan-later-list' : null
		]
			.filter((id): id is string => id !== null)
			.join(' ')
	);
</script>

{#snippet candidateRow(c: PlanCandidate)}
	{@const detail = candidateDetail(c, plan!)}
	<li class="flex items-baseline justify-between gap-2 py-0.5 text-sm" data-verdict={c.verdict}>
		<span class="min-w-0">
			{scenarioText(c.scenario)}{#if c.verdict === 'chosen'}{@const what = actDetail(c.scenario, c.params, plan!)}{#if what}<span class="text-fg-faint">{` · ${what}`}</span>{/if}{/if}
		</span>
		<Pill tone={verdictTone(c.verdict)}>
			{verdictText(c.verdict)}{detail ? ` · ${detail}` : ''}
		</Pill>
	</li>
{/snippet}

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

<section class="card" aria-labelledby="plan-title">
	<h2 id="plan-title" class="card-title">План бота</h2>
	{#if !plan}
		{#if error}
			<p class="text-sm text-bad-fg" role="alert">План недоступен: {errorText(error)}.</p>
		{:else}
			<p class="text-sm text-fg-muted" role="status">Загрузка плана…</p>
		{/if}
	{:else if view}
		<div class="grid gap-4 md:grid-cols-2">
			<div class="min-w-0 space-y-4">
				<section aria-labelledby="plan-now">
					<h3 id="plan-now" class="mb-1 text-xs font-semibold tracking-wide text-fg-muted uppercase">Сейчас</h3>
					{#if view.blockers.length > 0}
						{#each view.blockers as line (line)}<p class="font-semibold text-warn-fg">{line}</p>{/each}
						<p class="text-sm">
							{view.decision}{#if view.at}{' — следующий шаг в '}<b>{fmtTime(view.at)}</b>{/if}
						</p>
					{:else}
						<p class="font-semibold">
							{view.decision}{#if view.at}<span class="font-normal">{' — следующий шаг в '}</span><b>{fmtTime(view.at)}</b>{/if}
						</p>
						{#if view.then}<p class="text-sm">{view.then}</p>{/if}
					{/if}
					<p class="text-xs text-fg-muted">{view.phase}</p>
					{#if view.reserves}<p class="text-xs text-fg-muted">{view.reserves}</p>{/if}
					<p class="mt-1 text-xs text-fg-muted">{why}</p>
					{#if error}<p class="mt-1 text-xs text-bad-fg">Не обновилось: {errorText(error)}</p>{/if}
				</section>

				<section id="plan-why-panel" aria-labelledby="plan-why" class={extra}>
					<h3 id="plan-why" class="mb-1 text-xs font-semibold tracking-wide text-fg-muted uppercase">
						Почему не другое
					</h3>
					{#if plan.considered.length === 0}
						<p class="text-sm text-fg-faint">Других вариантов в этом решении не было.</p>
					{:else}
						<ul>
							{#each plan.considered as c, i (i)}{@render candidateRow(c)}{/each}
						</ul>
					{/if}
					{#if plan.basis && plan.basis.considered.length > 0}
						<h4 class="mt-2 text-xs text-fg-faint">{basis}</h4>
						<ul>
							{#each plan.basis.considered as c, i (i)}{@render candidateRow(c)}{/each}
						</ul>
					{/if}
				</section>

				{#if plan.also_ready.length > 0}
					<section id="plan-ready-panel" aria-labelledby="plan-ready" class={extra}>
						<h3 id="plan-ready" class="mb-1 text-xs font-semibold tracking-wide text-fg-muted uppercase">
							Готово сейчас <span class="font-normal normal-case">· {basis || 'на текущем снимке'}</span>
						</h3>
						<ul class="text-sm">
							{#each plan.also_ready as a (a.scenario + JSON.stringify(a.params))}
								{@const what = actDetail(a.scenario, a.params, plan)}
								<li class="py-0.5">{scenarioText(a.scenario)}{#if what}<span class="text-fg-faint">{` · ${what}`}</span>{/if}</li>
							{/each}
						</ul>
						<p class="text-xs text-fg-faint">Не очередь: запуск текущего шага меняет состояние.</p>
					</section>
				{/if}
			</div>

			<section aria-labelledby="plan-next" class="min-w-0">
				<h3 id="plan-next" class="mb-1 text-xs font-semibold tracking-wide text-fg-muted uppercase">
					Дальше по времени {#if basis}<span class="font-normal normal-case">· {basis}</span>{/if}
				</h3>
				{#if timers.length + later.length === 0}
					<p class="text-sm text-fg-faint">Таймеров нет: бот ждёт событий.</p>
				{/if}
				<ul id="plan-timers-list">
					{#each timers as t, i (t.kind + (t.key ?? ''))}
						{@render timerRow(t, i)}
					{/each}
				</ul>
				{#if later.length > 0}
					<h4 class="mt-2 text-xs text-fg-faint {timers.length >= PHONE_TIMERS && !more ? 'hidden md:block' : ''}">
						после пробуждения
					</h4>
					<ul id="plan-later-list">
						{#each later as t, i (t.kind + (t.key ?? ''))}
							{@render timerRow(t, timers.length + i)}
						{/each}
					</ul>
				{/if}
			</section>
		</div>
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
</section>
