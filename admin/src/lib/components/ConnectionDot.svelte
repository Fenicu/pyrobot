<script lang="ts">
	import type { LiveStatus } from '$lib/live/connection.svelte';

	interface Props {
		status: LiveStatus;
		retryIn?: number;
		compact?: boolean;
		/** Движок аккаунта не запущен: поток событий ему не открыть — это не обрыв связи. */
		stopped?: boolean;
	}
	let { status, retryIn = 0, compact = false, stopped = false }: Props = $props();

	const view = $derived(
		stopped
			? { color: 'bg-zinc-500', text: 'движок не запущен' }
			: status === 'open'
				? { color: 'bg-emerald-500', text: 'связь есть' }
				: status === 'offline'
					? {
							color: 'bg-red-500',
							text: retryIn > 0 ? `нет связи · повтор через ${Math.round(retryIn / 1000)} с` : 'нет связи'
						}
					: status === 'idle'
						? { color: 'bg-zinc-500', text: 'не подключено' }
						: { color: 'bg-amber-400', text: 'переподключение' }
	);
</script>

<span class="inline-flex items-center gap-1.5 text-xs text-fg-muted" title={view.text}>
	<span class="inline-block size-2 rounded-full {view.color}" aria-hidden="true"></span>
	<span class={compact ? 'sr-only' : ''}>{view.text}</span>
</span>
