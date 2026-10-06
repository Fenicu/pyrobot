<script lang="ts">
	import { untrack } from 'svelte';
	import type { GadgetOut, GadgetsOut, UpgradeChoice } from '$lib/api/types';
	import { KIND_MARK, MAX_LEVEL, UPGRADE_KINDS, defaultTarget, lossOnFail } from '$lib/gadgets/text';
	import Modal from '../Modal.svelte';

	interface Props {
		gadget: GadgetOut;
		upgrades: GadgetsOut['upgrades'];
		info: GadgetsOut['upgrade_info'];
		busy: boolean;
		onsubmit: (target: number, kind: UpgradeChoice) => void;
		onclose: () => void;
	}
	let { gadget, upgrades, info, busy, onsubmit, onclose }: Props = $props();

	const level = $derived(gadget.level ?? 0);
	// Окно открывается заново на каждый гаджет: цель по умолчанию — от уровня при открытии.
	let target = $state(untrack(() => defaultTarget(gadget.level ?? 0)));
	let kind = $state<UpgradeChoice>('auto');
	const valid = $derived(Number.isInteger(target) && target > level && target <= MAX_LEVEL);
	const vip = $derived(gadget.grade?.startsWith('⚫') ?? false);

	function kindLabel(k: UpgradeChoice): string {
		if (k === 'auto') return 'авто: ⚪️ до порога из настроек, дальше 🔴, кончились 🔴 — 🔵';
		const left = upgrades ? ` · запас ${upgrades[k]}` : '';
		const chance = info?.chances[k];
		return `${KIND_MARK[k]}${left}${chance !== undefined ? ` · шанс ${chance}%` : ''}`;
	}

	function submit(e: SubmitEvent) {
		e.preventDefault();
		if (valid) onsubmit(target, kind);
	}
</script>

<Modal title={`Точить: ${gadget.slot} ${gadget.name}`} {onclose}>
	<form id="gadget-upgrade" class="space-y-3 text-sm" onsubmit={submit}>
		<p>Сейчас: {gadget.grade ?? ''}{level} ур.</p>
		<label class="block">
			<span class="mb-1 block">Цель — уровень</span>
			<input
				type="number"
				class="input w-24"
				min={level + 1}
				max={MAX_LEVEL}
				step="1"
				required
				bind:value={target}
				data-autofocus
			/>
		</label>
		<fieldset class="space-y-1">
			<legend class="mb-1">Улучшения</legend>
			{#each UPGRADE_KINDS as k (k)}
				<label class="flex cursor-pointer items-center gap-2">
					<input type="radio" name="kind" value={k} bind:group={kind} />
					<span>{kindLabel(k)}</span>
				</label>
			{/each}
		</fieldset>
		<ul class="list-disc space-y-1 pl-5 text-fg-muted">
			<li>Провал на ур. {level} теряет {lossOnFail(level)} ур.; дальше — четверть уровня на момент попытки.</li>
			{#if vip}<li class="text-bad-fg">Провал снимет ⚫️Сет VIP.</li>{/if}
			<li>Бот точит порциями до 20 попыток, вне метро, Горбушки и окна битвы.</li>
		</ul>
	</form>
	{#snippet footer()}
		<button type="button" class="btn" onclick={onclose}>Отмена</button>
		<button type="submit" form="gadget-upgrade" class="btn btn-danger" disabled={busy || !valid}>Точить</button>
	{/snippet}
</Modal>
