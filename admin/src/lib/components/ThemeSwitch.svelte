<script lang="ts">
	import Monitor from '@lucide/svelte/icons/monitor';
	import Moon from '@lucide/svelte/icons/moon';
	import Sun from '@lucide/svelte/icons/sun';
	import { theme, type ThemePref } from '$lib/stores/theme.svelte';
	import Tile from './ui/Tile.svelte';

	interface Props {
		/** Одна кнопка с текущей темой, нажатие — следующая по кругу: icon — значок полосы ПК,
		 * tile — плитка меню телефона. */
		variant: 'icon' | 'tile';
	}
	let { variant }: Props = $props();

	const options: { value: ThemePref; label: string; icon: typeof Moon }[] = [
		{ value: 'dark', label: 'Тёмная', icon: Moon },
		{ value: 'light', label: 'Светлая', icon: Sun },
		{ value: 'system', label: 'Как в системе', icon: Monitor }
	];
	const index = $derived(Math.max(0, options.findIndex((o) => o.value === theme.pref)));
	const active = $derived(options[index]!);
	const label = $derived(`Тема: ${active.label.toLowerCase()}`);

	function next() {
		theme.set(options[(index + 1) % options.length]!.value);
	}
</script>

{#if variant === 'tile'}
	<Tile icon={active.icon} {label} onclick={next} />
{:else}
	<button
		type="button"
		class="btn btn-ghost size-[34px] min-h-0 p-0"
		aria-label={label}
		title={label}
		onclick={next}
	>
		<active.icon class="size-4" aria-hidden="true" />
	</button>
{/if}
