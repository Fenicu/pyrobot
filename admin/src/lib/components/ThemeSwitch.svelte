<script lang="ts">
	import Monitor from '@lucide/svelte/icons/monitor';
	import Moon from '@lucide/svelte/icons/moon';
	import Sun from '@lucide/svelte/icons/sun';
	import { theme, type ThemePref } from '$lib/stores/theme.svelte';

	interface Props {
		/** Одна кнопка с текущей темой; нажатие — следующая по кругу. */
		compact?: boolean;
	}
	let { compact = false }: Props = $props();

	const options: { value: ThemePref; label: string; icon: typeof Moon }[] = [
		{ value: 'dark', label: 'Тёмная', icon: Moon },
		{ value: 'light', label: 'Светлая', icon: Sun },
		{ value: 'system', label: 'Как в системе', icon: Monitor }
	];
	const index = $derived(Math.max(0, options.findIndex((o) => o.value === theme.pref)));
	const active = $derived(options[index]!);
</script>

{#if compact}
	{@const label = `Тема: ${active.label.toLowerCase()}`}
	<button
		type="button"
		class="btn btn-ghost size-[34px] min-h-0 p-0"
		aria-label={label}
		title={label}
		onclick={() => theme.set(options[(index + 1) % options.length]!.value)}
	>
		<active.icon class="size-4" aria-hidden="true" />
	</button>
{:else}
	<div class="flex gap-1" role="group" aria-label="Тема">
		{#each options as o (o.value)}
			<button
				type="button"
				class="btn btn-ghost min-h-8 px-2"
				aria-pressed={theme.pref === o.value}
				aria-label={o.label}
				title={o.label}
				onclick={() => theme.set(o.value)}
			>
				<o.icon class="size-4 {theme.pref === o.value ? 'text-accent' : ''}" aria-hidden="true" />
			</button>
		{/each}
	</div>
{/if}
