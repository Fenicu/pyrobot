<script lang="ts">
	import type { FeedFilter, FeedType } from '$lib/stores/journal.svelte';
	import { ACTION_STATUS, SOURCE } from '$lib/util/game';

	interface Props {
		filter: FeedFilter;
		onchange: (next: Partial<FeedFilter>) => void;
	}
	let { filter, onchange }: Props = $props();

	const TYPES: { value: FeedType | null; label: string }[] = [
		{ value: null, label: 'все' },
		{ value: 'decision', label: 'решения' },
		{ value: 'action', label: 'действия' },
		{ value: 'message', label: 'сообщения' }
	];
	const PROBLEMS = ['refused', 'rejected', 'outcome_unknown', 'suppressed'];
	// Статус и источник есть только у действий: с ними лента — только действия.
	const actionsOnly = $derived(filter.status !== null || filter.source !== null);

	function setType(value: FeedType | null) {
		onchange(value === 'action' ? { type: value } : { type: value, status: null, source: null });
	}
</script>

<!-- На телефоне — одна строка с горизонтальной прокруткой чипов. -->
<div
	class="flex items-center gap-1.5 overflow-x-auto pb-1 [scrollbar-width:none] *:shrink-0 md:flex-wrap md:overflow-visible md:pb-0"
	role="group"
	aria-label="Фильтры журнала"
>
	{#each TYPES as t (t.label)}
		<button
			type="button"
			class="chip"
			aria-pressed={(actionsOnly ? 'action' : filter.type) === t.value}
			onclick={() => setType(t.value)}>{t.label}</button
		>
	{/each}
	<label class="chip gap-1 pr-1">
		<span>ошибки</span>
		<select
			class="bg-transparent text-xs text-fg focus:outline-none"
			value={filter.status ?? ''}
			onchange={(e) => onchange({ status: e.currentTarget.value || null, type: 'action' })}
		>
			<option value="">все</option>
			{#each PROBLEMS as s (s)}<option value={s}>{ACTION_STATUS[s]}</option>{/each}
		</select>
	</label>
	<label class="chip gap-1 pr-1">
		<span>источник</span>
		<select
			class="bg-transparent text-xs text-fg focus:outline-none"
			value={filter.source ?? ''}
			onchange={(e) => onchange({ source: e.currentTarget.value || null, type: 'action' })}
		>
			<option value="">любой</option>
			{#each Object.entries(SOURCE) as [value, label] (value)}<option {value}>{label}</option>{/each}
		</select>
	</label>
	<label class="chip gap-1 pr-1">
		<span>с</span>
		<input
			type="date"
			class="bg-transparent text-xs text-fg focus:outline-none"
			value={filter.since ?? ''}
			onchange={(e) => onchange({ since: e.currentTarget.value || null })}
		/>
	</label>
	<label class="chip gap-1 pr-1">
		<span>по</span>
		<input
			type="date"
			class="bg-transparent text-xs text-fg focus:outline-none"
			value={filter.until ?? ''}
			onchange={(e) => onchange({ until: e.currentTarget.value || null })}
		/>
	</label>
</div>
