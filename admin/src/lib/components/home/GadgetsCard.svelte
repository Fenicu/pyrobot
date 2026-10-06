<script lang="ts">
	import type { AccountApi } from '$lib/api/account';
	import { errorText, type ApiError } from '$lib/api/errors';
	import type { EngineStatus, GadgetOut, GadgetsOut, PublicState, UpgradeChoice } from '$lib/api/types';
	import { startUpgrade, stopUpgrade } from '$lib/gadgets/actions';
	import {
		BAG_FULL,
		MAX_LEVEL,
		SLOT_ICON,
		actionLine,
		bagFull,
		buyLine,
		moneyLine,
		resultLine,
		targetLine,
		taskLine,
		upgradeError
	} from '$lib/gadgets/text';
	import { dialogs } from '$lib/stores/confirm.svelte';
	import { toasts } from '$lib/stores/toasts.svelte';
	import { SKILL_MARK } from '$lib/util/game';
	import { val } from '$lib/util/observed';
	import UpgradeDialog from './UpgradeDialog.svelte';

	interface Props {
		api: AccountApi;
		state: PublicState;
		stale: string[];
		/** `GET /gadgets`: план покупки, задача заточки и её ход; null — ещё не загружено. */
		gadgets: GadgetsOut | null;
		error: ApiError | null;
		status: EngineStatus | null;
		/** Ответ действия — новый вид карточки. */
		onchange: (out: GadgetsOut) => void;
	}
	let { api, state: snapshot, stale, gadgets: out, error, status, onchange }: Props = $props();
	let busy = $state(false);
	let failure = $state<string | null>(null);
	let choosing = $state<GadgetOut | null>(null);

	const gadgets = $derived(val(snapshot, 'gadgets'));
	const isStale = $derived(stale.includes('gadgets'));
	// Без движка и в dry_run клики заточки не уйдут в игру — кнопки нет.
	const canUpgrade = $derived(status?.running === true && status.mode === 'live');
	const task = $derived(out?.task ?? null);
	const active = $derived(task?.status === 'active');
	const taskText = $derived(out ? taskLine(out) : null);
	const resultText = $derived(out ? resultLine(out) : null);
	const buy = $derived(out?.buy.enabled ? out : null);

	const bonuses = (b: Record<string, number>) =>
		Object.entries(b)
			.map(([skill, n]) => `+${n}${SKILL_MARK[skill] ?? skill}`)
			.join(' ');

	/** Тот же гаджет в ответе `/gadgets`: у него слот заточки. */
	function upgradable(g: { slot: string; name: string }): GadgetOut | null {
		const found = out?.worn.find((w) => w.slot === g.slot && w.name === g.name && w.up_slot !== null);
		return found && (found.level ?? 0) < MAX_LEVEL ? found : null;
	}

	function busyHint(w: GadgetOut): string | undefined {
		if (!active || !task) return undefined;
		return task.slot === w.up_slot
			? 'Этот гаджет уже точится'
			: `Идёт заточка ${task.slot ? SLOT_ICON[task.slot] : ''} ${task.gadget ?? ''} — сначала «Стоп»`;
	}

	async function act(action: () => Promise<GadgetsOut>, done: string) {
		busy = true;
		failure = null;
		try {
			onchange(await action());
			toasts.show(done, 'ok');
		} catch (e) {
			failure = upgradeError(e);
		} finally {
			busy = false;
		}
	}

	async function launch(target: number, kind: UpgradeChoice) {
		const w = choosing;
		if (!w?.up_slot) return;
		const slot = w.up_slot;
		choosing = null;
		await act(() => startUpgrade(api, slot, target, kind), 'Заточка запущена: бот точит порциями');
	}

	async function stop() {
		const ok = await dialogs.confirm({
			title: 'Остановить заточку?',
			body: 'Следующая попытка не уйдёт; уже сделанные останутся.',
			confirmText: 'Остановить',
			cancelText: 'Не останавливать',
			danger: true
		});
		if (ok) await act(() => stopUpgrade(api), 'Заточка остановлена');
	}
</script>

<section class="card" aria-labelledby="gadgets-title">
	<h2 id="gadgets-title" class="card-title">Гаджеты при тебе</h2>
	{#if failure}<p class="mb-2 text-sm text-bad-fg" role="alert">{failure}</p>{/if}
	<div class="text-sm {isStale ? 'text-fg-faint' : ''}" title={isStale ? 'устарело' : undefined}>
		{#if gadgets === null}
			<p class="text-fg-faint">нет данных</p>
		{:else if gadgets.items.length === 0}
			<p class="text-fg-muted">ничего не надето</p>
		{:else}
			<ul class="space-y-0.5">
				{#each gadgets.items as g, i (i)}
					{@const w = canUpgrade ? upgradable(g) : null}
					<li class="flex flex-wrap items-center gap-x-2">
						<span>
							{g.slot} {g.name} {g.grade ?? ''}{g.level ?? ''}
							{#if Object.keys(g.bonuses).length > 0 || g.mark}·{/if}
							{bonuses(g.bonuses)}{#if g.mark}{` ${g.mark}`}{/if}
						</span>
						{#if w}
							<button
								type="button"
								class="btn px-2 py-0 text-xs"
								disabled={busy || active}
								title={busyHint(w)}
								aria-label={`Точить: ${g.slot} ${g.name}`}
								onclick={() => (choosing = w)}>Точить</button
							>
						{/if}
					</li>
				{/each}
			</ul>
			{#if canUpgrade && active}
				<p class="text-xs text-fg-muted">Другой гаджет — после конца или «Стоп» текущей заточки.</p>
			{/if}
			{#if gadgets.sets.length > 0}
				<p class="mt-2 text-fg-muted">{gadgets.sets.join(' · ')}</p>
			{/if}
		{/if}
		{#if isStale}<span class="sr-only"> (устарело)</span>{/if}
		{#if out && out.bag.used !== null && out.bag.cap !== null}
			<p class="mt-1 text-fg-muted">Рюкзак: {out.bag.used}/{out.bag.cap}</p>
		{/if}
	</div>

	{#if active && taskText}
		<div class="mt-3 space-y-1 border-t border-line-soft pt-2 text-sm" aria-label="Заточка" role="group">
			<p>{taskText}</p>
			{#if out?.upgrades}
				<p class="text-xs text-fg-muted">
					запас ⚪️{out.upgrades.white} 🔵{out.upgrades.blue} 🔴{out.upgrades.red}{#if out.upgrade_info && Object.keys(out.upgrade_info.chances).length > 0}
						{` · шансы ⚪️${out.upgrade_info.chances.white ?? '?'}% 🔵${out.upgrade_info.chances.blue ?? '?'}% 🔴${out.upgrade_info.chances.red ?? '?'}%`}{/if}
				</p>
			{/if}
			<button type="button" class="btn" disabled={busy || !status?.running} onclick={stop}>Стоп</button>
		</div>
	{:else if resultText}
		<p class="mt-3 border-t border-line-soft pt-2 text-sm">{resultText}</p>
	{/if}

	{#if buy}
		<div class="mt-3 space-y-1 border-t border-line-soft pt-2 text-sm" aria-label="Покупка гаджетов" role="group">
			{#if bagFull(buy)}<p class="text-warn-fg">{BAG_FULL}</p>{/if}
			{#if buy.buy.plan === null}
				<p class="text-fg-muted">Покупка гаджетов: резерв неизвестен — бот сначала обновит экраны.</p>
			{:else}
				{@const line = buyLine(buy)}
				{@const next = actionLine(buy)}
				{#if line}<p>{line}</p>{:else if buy.buy.money}<p class="text-fg-muted">Деньги на покупку: {moneyLine(buy.buy.money)}</p>{/if}
				{#if next}<p class="text-fg-muted">{next}</p>{/if}
				{#each buy.buy.plan.candidates as t (t.set)}
					{@const blocked = targetLine(t)}
					{#if blocked}<p class="text-fg-muted">{blocked}</p>{/if}
				{/each}
			{/if}
		</div>
	{/if}
	{#if !out && error}
		<p class="mt-2 text-xs text-fg-muted">Заточка и покупка недоступны: {errorText(error)}</p>
	{/if}
</section>

{#if choosing && out}
	<UpgradeDialog
		gadget={choosing}
		upgrades={out.upgrades}
		info={out.upgrade_info}
		{busy}
		onsubmit={launch}
		onclose={() => (choosing = null)}
	/>
{/if}
