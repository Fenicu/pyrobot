<script lang="ts">
	import ChevronLeft from '@lucide/svelte/icons/chevron-left';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Plus from '@lucide/svelte/icons/plus';

	interface Props {
		value: string[];
		/** Варианты (массив enum); без них — свободный ввод. */
		options?: string[];
		label: string;
		/** Подписи значений (код → текст); нет — показывается код. */
		labels?: Record<string, string>;
		disabled?: boolean;
		/** id описания поля (`SettingField`, `{id}-help`): связывает поле добавления тега с ним на ПК. */
		describedby?: string;
		onchange: (next: string[]) => void;
	}
	let { value, options, label, labels, disabled = false, describedby, onchange }: Props = $props();
	const caption = (v: string) => labels?.[v] ?? v;
	let text = $state('');
	const rest = $derived(options?.filter((o) => !value.includes(o)) ?? []);

	function move(i: number, d: number) {
		const next = [...value];
		const [item] = next.splice(i, 1);
		next.splice(i + d, 0, item!);
		onchange(next);
	}

	function add(item: string) {
		const v = item.trim();
		if (v && !value.includes(v)) onchange([...value, v]);
		text = '';
	}
</script>

<div class="flex flex-wrap items-center gap-1" role="group" aria-label={label}>
	{#each value as tag, i (tag)}
		<span class="inline-flex items-center gap-0.5 rounded-full border border-line bg-surface-2 py-0.5 pr-1 pl-2 text-xs">
			<span class="ext-text">{caption(tag)}</span>
			<button
				type="button"
				class="rounded p-0.5 hover:bg-bg disabled:opacity-30"
				aria-label="{caption(tag)}: раньше"
				disabled={disabled || i === 0}
				onclick={() => move(i, -1)}><ChevronLeft class="size-3" aria-hidden="true" /></button
			>
			<button
				type="button"
				class="rounded p-0.5 hover:bg-bg disabled:opacity-30"
				aria-label="{caption(tag)}: позже"
				disabled={disabled || i === value.length - 1}
				onclick={() => move(i, 1)}><ChevronRight class="size-3" aria-hidden="true" /></button
			>
			<button
				type="button"
				class="rounded px-1 hover:bg-bg"
				aria-label="Убрать {caption(tag)}"
				{disabled}
				onclick={() => onchange(value.filter((t) => t !== tag))}>✕</button
			>
		</span>
	{/each}
	{#if options}
		{#if rest.length > 0}
			<label class="inline-flex items-center gap-1 rounded-full border border-dashed border-line px-2 py-0.5 text-xs">
				<Plus class="size-3" aria-hidden="true" />
				<span class="sr-only">Добавить в «{label}»</span>
				<select
					class="bg-transparent text-xs focus:outline-none"
					value=""
					{disabled}
					aria-describedby={describedby}
					onchange={(e) => {
						add(e.currentTarget.value);
						e.currentTarget.value = '';
					}}
				>
					<option value="" disabled>добавить</option>
					{#each rest as o (o)}<option value={o}>{caption(o)}</option>{/each}
				</select>
			</label>
		{/if}
	{:else}
		<input
			class="input min-h-7 w-32 text-xs"
			placeholder="добавить…"
			aria-label="Добавить в «{label}»"
			aria-describedby={describedby}
			bind:value={text}
			{disabled}
			onkeydown={(e) => {
				if (e.key === 'Enter') {
					e.preventDefault();
					add(text);
				}
			}}
		/>
	{/if}
</div>
