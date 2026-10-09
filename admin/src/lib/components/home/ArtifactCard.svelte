<script lang="ts">
	import type { AccountApi } from '$lib/api/account';
	import { ApiFailure, errorText, type ApiError } from '$lib/api/errors';
	import type { ArtifactOut } from '$lib/api/types';
	import { adoptCollect, cancelCollect, pauseCollect, resumeCollect, startCollect } from '$lib/artifact/actions';
	import { RECOLLECT, RUN_STATUS, artifactLabel, deedsLine, leftText, type ArtifactKey } from '$lib/artifact/text';
	import { dialogs } from '$lib/stores/confirm.svelte';
	import { toasts } from '$lib/stores/toasts.svelte';
	import { fmtMoment } from '$lib/util/format';
	import Meter from '../Meter.svelte';
	import Modal from '../Modal.svelte';
	import Pill from '../Pill.svelte';
	import Card from '../ui/Card.svelte';

	interface Props {
		api: AccountApi;
		artifact: ArtifactOut | null;
		error: ApiError | null;
		now: Date;
		/** Ответ действия — новый вид карточки. */
		onchange: (out: ArtifactOut) => void;
	}
	let { api, artifact, error, now, onchange }: Props = $props();
	let busy = $state(false);
	let choosing = $state<ArtifactKey | null>(null);
	let lotteryMax = $state(true);
	let failure = $state<string | null>(null);

	const run = $derived(artifact?.run ?? null);
	const going = $derived(run?.status === 'active' || run?.status === 'paused');
	const locked = $derived(artifact?.next_start_at ? new Date(artifact.next_start_at) > now : false);
	const startable = $derived.by(() => {
		if (!artifact || !run || locked || artifact.collecting) return [];
		if (run.status === 'starting' || run.status === 'active' || run.status === 'paused') return [];
		return RECOLLECT.filter((k) => (artifact.levels[k] ?? 0) < 100);
	});

	async function act(action: () => Promise<ArtifactOut>, done: string) {
		busy = true;
		failure = null;
		try {
			onchange(await action());
			toasts.show(done, 'ok');
		} catch (e) {
			failure = e instanceof ApiFailure ? e.message : String(e);
		} finally {
			busy = false;
		}
	}

	function startLabel(key: ArtifactKey): string {
		const level = artifact?.levels[key];
		return `Запустить сбор: ${artifactLabel(key)}${level !== undefined ? ` (${level}/100)` : ''}`;
	}

	function open(key: ArtifactKey) {
		lotteryMax = artifact?.lottery_on_start ?? true;
		choosing = key;
	}

	async function launch(e: SubmitEvent) {
		e.preventDefault();
		const key = choosing;
		if (!key || !artifact) return;
		const lottery = !artifact.lottery_on && lotteryMax;
		choosing = null;
		await act(() => startCollect(api, key, lottery), 'Сбор запрошен: бот запустит его, когда персонаж освободится');
	}

	async function cancel() {
		if (!run) return;
		const body =
			run.status === 'starting'
				? 'Запуск отменится: в игре сбор ещё не начат.'
				: `Бот вернётся к обычной игре, сбор в игре дотикает. Следующий сбор — не раньше ${fmtMoment(run.ends_at, now)}.`;
		const ok = await dialogs.confirm({
			title: 'Отменить сбор?',
			body,
			confirmText: 'Отменить сбор',
			cancelText: 'Не отменять',
			danger: true
		});
		if (ok) await act(() => cancelCollect(api), 'Сбор отменён');
	}
</script>

<Card title="Сбор артефакта">
	{#if failure}<p class="mb-2 text-sm text-bad-fg" role="alert">{failure}</p>{/if}
	{#if !artifact || !run}
		<p class="text-sm text-fg-muted">{error ? `Недоступно: ${errorText(error)}` : 'Загрузка…'}</p>
	{:else if run.status === 'starting'}
		<p class="text-sm">
			Запуск: {artifactLabel(run.artifact)} — бот начнёт сбор, как только персонаж освободится (не дело, не сон,
			не метро).
		</p>
		<div class="mt-2 flex flex-wrap gap-2">
			<button type="button" class="btn" disabled={busy} onclick={cancel}>Отменить</button>
		</div>
	{:else if going}
		<p class="flex flex-wrap items-center gap-1.5 text-sm">
			<span>{artifactLabel(run.artifact)}</span>
			<span class="font-medium">{artifact.level ?? '?'}/100</span>
			{#if run.status === 'paused'}<Pill tone="warn">пауза — бот играет как обычно</Pill>{/if}
		</p>
		<Meter value={artifact.level ?? 0} max={100} label="Уровень артефакта" />
		<p class="text-xs text-fg-muted">{leftText(run.ends_at, now)} · до {fmtMoment(run.ends_at, now)}</p>
		{#if artifact.pace.levels_per_day !== null}
			<p class="text-xs text-fg-muted">
				темп {artifact.pace.levels_per_day} ур./сутки · прогноз {artifact.pace.forecast_level}/100
			</p>
		{/if}
		<p class="text-xs text-fg-muted">{deedsLine(artifact.tactic[run.artifact ?? ''] ?? [])}</p>
		<div class="mt-2 flex flex-wrap gap-2">
			{#if run.status === 'active'}
				<button type="button" class="btn" disabled={busy}
					onclick={() => act(() => pauseCollect(api), 'Сбор на паузе: бот играет как обычно')}>Пауза</button>
			{:else}
				<button type="button" class="btn" disabled={busy}
					onclick={() => act(() => resumeCollect(api), 'Сбор продолжается')}>Продолжить</button>
			{/if}
			<button type="button" class="btn" disabled={busy} onclick={cancel}>Отменить</button>
		</div>
	{:else}
		{#if run.status === 'cancelled' || run.status === 'finished'}
			<p class="text-sm">Итог: {artifactLabel(run.artifact)} — {run.result_level ?? '?'}/100 ({RUN_STATUS[run.status]})</p>
		{/if}
		{#if artifact.collecting && artifact.external}
			<p class="text-sm">
				В игре идёт сбор {artifactLabel(artifact.collecting.artifact)} до {fmtMoment(artifact.collecting.ends_at, now)} —
				бот его не ведёт.
			</p>
			<div class="mt-2">
				<button type="button" class="btn" disabled={busy} onclick={() => act(() => adoptCollect(api), 'Бот ведёт сбор')}
					>Вести сбор</button>
			</div>
		{/if}
		{#if locked && artifact.next_start_at}
			<p class="text-xs text-fg-muted">Следующий сбор — с {fmtMoment(artifact.next_start_at, now)}</p>
		{/if}
		{#if startable.length > 0}
			<div class="mt-2 flex flex-wrap gap-2">
				{#each startable as key (key)}
					<button type="button" class="btn max-w-full py-1.5 text-left whitespace-normal" disabled={busy} onclick={() => open(key)}>{startLabel(key)}</button>
				{/each}
			</div>
		{:else if !locked && !artifact.collecting}
			<p class="text-xs text-fg-muted">Все пересобираемые артефакты — 100 уровня.</p>
		{/if}
	{/if}
</Card>

{#if choosing && artifact}
	{@const key = choosing}
	<Modal title={`Запустить сбор: ${artifactLabel(key)}`} onclose={() => (choosing = null)}>
		<form id="artifact-start" class="space-y-3 text-sm" onsubmit={launch}>
			<p>Сейчас: {artifact.levels[key] ?? '?'} ур.</p>
			<ul class="list-disc space-y-1 pl-5 text-fg-muted">
				<li>Уровень артефакта станет 0.</li>
				<li>🔥 мотивация обнулится.</li>
				<li>Сбор идёт 10 суток, остановить его в игре нельзя.</li>
				<li>Следующий сбор любого артефакта — только через 10 суток.</li>
			</ul>
			<p>Части — в делах: {deedsLine(artifact.tactic[key] ?? [])}</p>
			{#if !artifact.lottery_on}
				<label class="inline-flex cursor-pointer items-center gap-2">
					<input type="checkbox" role="switch" class="peer sr-only" bind:checked={lotteryMax}
						aria-label="Включить лотерею на максимум" />
					<span
						class="relative h-5 w-9 rounded-full bg-muted-bg transition-colors peer-checked:bg-accent peer-focus-visible:outline-2 peer-focus-visible:outline-accent after:absolute after:top-0.5 after:left-0.5 after:size-4 after:rounded-full after:bg-white after:transition-transform peer-checked:after:translate-x-4"
						aria-hidden="true"
					></span>
					<span>Включить лотерею на максимум на время сбора (после — вернуть как было)</span>
				</label>
			{/if}
		</form>
		{#snippet footer()}
			<button type="button" class="btn" onclick={() => (choosing = null)}>Отмена</button>
			<button type="submit" form="artifact-start" class="btn btn-danger" disabled={busy}>Запустить</button>
		{/snippet}
	</Modal>
{/if}
