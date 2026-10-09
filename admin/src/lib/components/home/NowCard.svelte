<script lang="ts">
	import { errorText, type ApiError } from '$lib/api/errors';
	import type { MetroLive, Outlook, PlanCandidate, PublicState } from '$lib/api/types';
	import { endText, shown } from '$lib/metro/live';
	import { accountHref } from '$lib/nav';
	import { basisText, explain, nowView } from '$lib/plan/now';
	import { actDetail, candidateDetail, scenarioText, verdictText, verdictTone } from '$lib/plan/text';
	import { fmtTime } from '$lib/util/format';
	import Pill from '../Pill.svelte';
	import Card from '../ui/Card.svelte';
	import MetroLiveCard from './MetroLiveCard.svelte';

	/** Живой забег метро: поля `MetroLiveStore`. */
	interface MetroRun {
		frame: MetroLive | null;
		receivedAt: number | null;
		metroRunId: number | null;
	}

	interface Props {
		plan: Outlook | null;
		error: ApiError | null;
		state: PublicState;
		now: Date;
		account: number;
		metro?: MetroRun | null;
	}
	// Снимок — для строки пояснения (задания дня); `state` занят руной.
	let { plan, error, state: snapshot, now, account, metro = null }: Props = $props();
	const uid = $props.id();
	const readyId = `${uid}-ready`;

	// «Почему не другое» свёрнуто на любой ширине; дальше — как его оставил человек.
	let whyOpen = $state(false);

	// Срок, до которого спит цикл, наступает между тиками часов главной (раз в 30 с): «Сейчас»
	// перерисовывается в сам срок, а план, пришедший уже после срока, сразу сверяется с настоящим
	// временем.
	let woke = $state<Date | null>(null);
	const clock = $derived(woke !== null && woke.getTime() > now.getTime() ? woke : now);
	$effect(() => {
		const wake = plan?.loop.wake_at;
		if (!wake) return;
		const left = Date.parse(wake) - Date.now();
		if (left <= 0) {
			woke = new Date();
			return;
		}
		const timer = setTimeout(() => (woke = new Date(wake)), left);
		return () => clearTimeout(timer);
	});
	// Идущий забег заменяет план картой; кончившийся (ещё в окне `shown`) — строка итога над планом.
	const visible = $derived(metro !== null && shown(metro.frame, metro.receivedAt, now.getTime()));
	const running = $derived(visible && metro?.frame?.running === true);
	const ended = $derived(visible && metro?.frame?.running === false ? metro.frame : null);
	// Виден другой запуск метро. Забег, не дошедший до конца, продолжается в том же сообщении
	// (персонаж ещё в метро) — это не новый забег; дошедший — итог прошлого.
	const endNote = $derived.by(() => {
		if (!ended || metro === null || metro.metroRunId === null || metro.metroRunId === ended.scenario_run_id) return '';
		return ended.outcome === 'finished' ? 'итог прошлого, идёт вход в новый' : 'забег продолжается';
	});
	// Движок не запущен (или ещё регистрируется) — это не сбой: приглушённо, как на плашке.
	const stopped = $derived(error?.kind === 'engine_down' && error.code !== 'planner not started');
	const view = $derived(plan ? nowView(plan, clock) : null);
	// Занятость устарела: кроме решения, план — второй проход по последним известным значениям.
	const basis = $derived(plan ? basisText(plan) : '');
	const why = $derived(plan ? explain(plan, snapshot, now) : '');
	const others = $derived(
		[...(plan?.considered ?? []), ...(plan?.basis?.considered ?? [])].filter((c) => c.verdict !== 'chosen').length
	);
</script>

{#snippet candidateRow(c: PlanCandidate)}
	{@const detail = candidateDetail(c, plan!)}
	<li class="flex items-baseline justify-between gap-2 border-b border-line-soft py-1 text-sm last:border-0" data-verdict={c.verdict}>
		<span class="min-w-0">
			{scenarioText(c.scenario)}{#if c.verdict === 'chosen'}{@const what = actDetail(c.scenario, c.params, plan!)}{#if what}<span class="text-fg-faint">{` · ${what}`}</span>{/if}{/if}
		</span>
		<Pill tone={verdictTone(c.verdict)}>
			{verdictText(c.verdict)}{detail ? ` · ${detail}` : ''}
		</Pill>
	</li>
{/snippet}

{#snippet badge()}
	<Pill tone="dec">🚇 забег</Pill>
{/snippet}

<Card title="Сейчас" action={running ? badge : undefined}>
	{#if running && metro?.frame}
		<MetroLiveCard frame={metro.frame} receivedAt={metro.receivedAt} {now} />
	{:else}
		{#if ended}
			<p class="mb-2 flex items-baseline justify-between gap-2 text-sm" role="status" aria-label="Итог забега">
				<span class="min-w-0">🚇 {endText(ended)}{#if endNote}<span class="text-fg-muted">{` · ${endNote}`}</span>{/if}</span>
				<a class="shrink-0 text-xs text-accent hover:underline" href={accountHref(account, '/metro')}>забеги →</a>
			</p>
		{/if}
		{@render planBody()}
	{/if}
</Card>

{#snippet planBody()}
	{#if !plan}
		{#if stopped}
			<p class="text-sm text-fg-muted" role="status">План недоступен: движок не запущен.</p>
		{:else if error}
			<p class="text-sm text-bad-fg" role="alert">План недоступен: {errorText(error)}.</p>
		{:else}
			<p class="text-sm text-fg-muted" role="status">Загрузка плана…</p>
		{/if}
	{:else if view}
		{#if view.blockers.length > 0}
			{#each view.blockers as line (line)}
				<p class="font-semibold text-warn-fg">{line}</p>
			{/each}
			{#if view.decision}
				<p class="text-sm">
					{view.decision}{#if view.at}{' — следующий шаг в '}<b>{fmtTime(view.at)}</b>{/if}
				</p>
			{/if}
		{:else}
			<p class="text-base font-semibold">
				{view.decision}{#if view.at}<span class="font-normal">{' — следующий шаг в '}</span><b>{fmtTime(view.at)}</b>{/if}
			</p>
			{#if view.then}<p class="text-sm">{view.then}</p>{/if}
		{/if}
		<p class="mt-1 text-xs text-fg-muted">{view.phase}</p>
		{#if view.reserves}<p class="text-xs text-fg-muted">{view.reserves}</p>{/if}
		<p class="mt-1 text-xs text-fg-muted">{why}</p>
		{#if error}<p class="mt-1 text-xs text-bad-fg">Не обновилось: {errorText(error)}</p>{/if}

		<details class="mt-2" bind:open={whyOpen}>
			<summary class="cursor-pointer py-1 text-xs text-fg-muted hover:text-fg">Почему не другое · {others}</summary>
			<section aria-label="Почему не другое">
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
				<section class="mt-3" aria-labelledby={readyId}>
					<h3 id={readyId} class="mb-1 text-xs font-semibold tracking-wide text-fg-muted uppercase">
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
		</details>
	{/if}
{/snippet}
