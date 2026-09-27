<script lang="ts">
	import type { Snippet } from 'svelte';
	import { onMount } from 'svelte';
	import { inertOutside } from '$lib/util/inert';

	interface Props {
		title: string;
		onclose: () => void;
		children: Snippet;
		footer?: Snippet;
		/** center — окно по центру; sheet — шторка снизу (на ПК — панель справа). */
		variant?: 'center' | 'sheet';
	}

	let { title, onclose, children, footer, variant = 'center' }: Props = $props();
	let box = $state<HTMLDivElement>();
	const titleId = `dlg-${Math.random().toString(36).slice(2, 10)}`;
	const FOCUSABLE =
		'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

	let root = $state<HTMLDivElement>();

	onMount(() => {
		const previous = document.activeElement as HTMLElement | null;
		// Фон недоступен мыши, клавиатуре и читалкам, пока окно открыто.
		const restore = root ? inertOutside(root) : () => {};
		const first = box?.querySelector<HTMLElement>('[data-autofocus]') ?? focusables()[0];
		(first ?? box)?.focus();
		return () => {
			restore();
			previous?.focus?.();
		};
	});

	function focusables(): HTMLElement[] {
		return box ? [...box.querySelectorAll<HTMLElement>(FOCUSABLE)] : [];
	}

	// Фокус не уходит из окна: Tab по кругу, Esc — закрыть.
	function onkeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			e.preventDefault();
			e.stopPropagation();
			onclose();
			return;
		}
		if (e.key !== 'Tab') return;
		const items = focusables();
		const first = items[0];
		const last = items[items.length - 1];
		if (!first || !last) {
			e.preventDefault();
			return;
		}
		if (e.shiftKey && document.activeElement === first) {
			e.preventDefault();
			last.focus();
		} else if (!e.shiftKey && document.activeElement === last) {
			e.preventDefault();
			first.focus();
		}
	}
</script>

<div
	bind:this={root}
	class="fixed inset-0 z-50 flex {variant === 'sheet'
		? 'items-end md:items-stretch md:justify-end'
		: 'items-center justify-center p-4'}"
>
	<button
		type="button"
		class="absolute inset-0 cursor-default bg-black/60"
		aria-label="Закрыть"
		tabindex="-1"
		onclick={onclose}
	></button>
	<div
		bind:this={box}
		role="dialog"
		aria-modal="true"
		aria-labelledby={titleId}
		tabindex="-1"
		{onkeydown}
		class="relative flex max-h-[90vh] w-full flex-col border border-line bg-surface shadow-xl {variant ===
		'sheet'
			? 'rounded-t-xl md:max-h-none md:w-[28rem] md:rounded-none md:border-y-0 md:border-r-0'
			: 'max-w-md rounded-lg'}"
	>
		<div class="flex items-center justify-between gap-2 border-b border-line-soft px-4 py-3">
			<h2 id={titleId} class="text-base font-semibold">{title}</h2>
			<button type="button" class="btn btn-ghost min-h-8 px-2" aria-label="Закрыть" onclick={onclose}
				>✕</button
			>
		</div>
		<div class="overflow-y-auto px-4 py-3">
			{@render children()}
		</div>
		{#if footer}
			<div class="flex flex-wrap justify-end gap-2 border-t border-line-soft px-4 py-3">
				{@render footer()}
			</div>
		{/if}
	</div>
</div>
