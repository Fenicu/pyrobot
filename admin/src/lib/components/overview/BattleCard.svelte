<script lang="ts">
	import type { BattleOrdersIn, HqBattle, HqCompany, HqTarget } from '$lib/api/types';
	import {
		allowedTargets,
		battleTitle,
		draftBody,
		draftKey,
		ordersKey,
		saveErrorText,
		toDraft,
		warningLines,
		type OrdersDraft
	} from '$lib/overview/battle';
	import type { SaveResult } from '$lib/overview/battle.svelte';
	import { accountTitle } from '$lib/util/game';

	interface Props {
		battle: HqBattle;
		/** Ближайшая битва (иначе следующая). */
		nearest: boolean;
		targets: HqTarget[];
		companies: HqCompany[];
		saving: boolean;
		/** Итог последнего сохранения; чужой битвы не показывается. */
		result: SaveResult | null;
		onsave: (battleAt: string, body: BattleOrdersIn) => void;
	}
	let { battle, nearest, targets, companies, saving, result, onsave }: Props = $props();

	const titleId = $props.id();
	const title = $derived(battleTitle(battle, nearest));
	const serverKey = $derived(ordersKey(battle.orders));
	// Что было на сервере, когда форма последний раз с ним сверялась: по расхождению видно правку.
	let base = $state<string | null>(null);
	let draft = $state<OrdersDraft>({ all: '', corps: {} });
	const dirty = $derived(base !== null && draftKey(draft) !== base);
	const locked = $derived(!battle.editable || saving);
	const warnings = $derived(warningLines(battle));
	const mine = $derived(result?.battleAt === battle.battle_at ? result : null);
	const hasOrders = $derived(serverKey !== ordersKey({ all: null, corporations: {} }));
	const names = $derived(new Map(battle.accounts.map((a) => [a.id, accountTitle({ ...a, team_tag: null })])));

	// Приказы сервера изменились (опрос, своё сохранение): нетронутая форма их принимает, правка не
	// затирается — кроме случая, когда сервер уже равен набранному.
	$effect.pre(() => {
		if (serverKey === base) return;
		if (base === null || !dirty || draftKey(draft) === serverKey) {
			draft = toDraft(battle.orders);
			base = serverKey;
		}
	});

	const REASON: Record<string, string> = {
		locked_elsewhere: 'на другом хосте',
		stopped: 'движок не запущен'
	};
	const notWoken = $derived(
		(mine?.notWoken ?? []).map((n) => `${names.get(n.account_id) ?? `#${n.account_id}`} (${REASON[n.reason] ?? n.reason})`)
	);

	function save() {
		onsave(battle.battle_at, draftBody(draft));
	}

	function cancel() {
		draft = { all: '', corps: {} };
		onsave(battle.battle_at, { all: null, corporations: {} });
	}
</script>

<section class="card" aria-labelledby={titleId}>
	<h2 id={titleId} class="card-title">{title}</h2>

	{#if warnings.length > 0}
		<ul class="mb-3 space-y-1 text-sm text-warn-fg" aria-label="Предупреждения">
			{#each warnings as line (line)}<li>{line}</li>{/each}
		</ul>
	{/if}

	<div class="grid gap-x-3 gap-y-2 sm:grid-cols-[max-content_minmax(0,1fr)_auto] sm:items-center">
		<label for="{titleId}-all" class="text-sm font-medium">Всем</label>
		<select id="{titleId}-all" class="input" bind:value={draft.all} disabled={locked}>
			<option value="">Не задавать</option>
			{#each targets as t (t.name)}<option value={t.name}>{t.name}</option>{/each}
		</select>
		<span></span>

		{#each companies as c (c.code)}
			<label for="{titleId}-{c.code}" class="text-sm">{c.name}</label>
			<select
				id="{titleId}-{c.code}"
				class="input"
				value={draft.corps[c.code] ?? ''}
				onchange={(e) => (draft.corps[c.code] = e.currentTarget.value)}
				disabled={locked}
			>
				<option value="">Не задавать</option>
				{#each allowedTargets(targets, c.code) as t (t.name)}<option value={t.name}>{t.name}</option>{/each}
			</select>
			<span class="text-xs text-fg-muted">{c.accounts} акк.</span>
		{/each}
	</div>

	<div class="mt-3 flex flex-wrap gap-2">
		<button type="button" class="btn btn-primary" disabled={locked || !dirty} onclick={save}>Сохранить</button>
		<button type="button" class="btn" disabled={locked || !hasOrders} onclick={cancel}>Отменить приказы</button>
	</div>

	{#if mine?.error}
		<p class="ext-text mt-2 text-sm text-bad-fg" role="alert">{saveErrorText(mine.error, companies)}</p>
	{:else if mine}
		<p class="mt-2 text-sm text-fg-muted" role="status">
			Сохранено. Движки разбужены: {mine.woken.length}{#if notWoken.length > 0}. Не разбужены: {notWoken.join(', ')}{/if}
		</p>
	{/if}
</section>
