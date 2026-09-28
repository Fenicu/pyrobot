<script lang="ts">
	import { toasts } from '$lib/stores/toasts.svelte';

	const tone = {
		info: 'border-line bg-surface-2 text-fg',
		ok: 'border-ok-bg bg-ok-bg text-ok-fg',
		warn: 'border-warn-bg bg-warn-bg text-warn-fg',
		error: 'border-bad-bg bg-bad-bg text-bad-fg'
	} as const;
</script>

<div
	class="pointer-events-none fixed inset-x-2 bottom-[calc(5rem_+_env(safe-area-inset-bottom))] z-[60] flex flex-col items-end gap-2 md:right-4 md:bottom-4 md:left-auto"
	role="status"
	aria-live="polite"
	data-modal-keep
>
	{#each toasts.items as t (t.id)}
		<div
			class="pointer-events-auto flex w-full max-w-sm items-start gap-2 rounded-md border px-3 py-2 text-sm shadow-lg {tone[
				t.kind
			]}"
		>
			<p class="ext-text flex-1">{t.text}</p>
			<button
				type="button"
				class="shrink-0 opacity-70 hover:opacity-100"
				aria-label="Скрыть сообщение"
				onclick={() => toasts.dismiss(t.id)}>✕</button
			>
		</div>
	{/each}
</div>
