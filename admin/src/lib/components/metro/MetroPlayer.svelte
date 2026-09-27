<script lang="ts">
	import Pause from '@lucide/svelte/icons/pause';
	import Play from '@lucide/svelte/icons/play';
	import SkipBack from '@lucide/svelte/icons/skip-back';
	import SkipForward from '@lucide/svelte/icons/skip-forward';
	import { onDestroy } from 'svelte';

	interface Props {
		max: number;
		step: number;
		onstep: (step: number) => void;
		/** Мс на шаг при проигрывании. */
		speedMs?: number;
	}
	let { max, step, onstep, speedMs = 120 }: Props = $props();
	let timer: ReturnType<typeof setInterval> | null = null;
	let playing = $state(false);

	function stop() {
		if (timer !== null) clearInterval(timer);
		timer = null;
		playing = false;
	}

	function play() {
		if (playing) return stop();
		let current = step >= max ? 0 : step;
		onstep(current);
		playing = true;
		timer = setInterval(() => {
			current += 1;
			onstep(Math.min(current, max));
			if (current >= max) stop();
		}, speedMs);
	}

	onDestroy(stop);
</script>

<div class="flex flex-wrap items-center gap-2" role="group" aria-label="Проигрывание">
	<button type="button" class="btn min-h-8 px-2" aria-label="В начало" onclick={() => (stop(), onstep(0))}>
		<SkipBack class="size-4" aria-hidden="true" />
	</button>
	<button type="button" class="btn min-h-8 px-2" aria-label="Шаг назад" onclick={() => (stop(), onstep(Math.max(0, step - 1)))}>‹</button>
	<button type="button" class="btn min-h-8 px-2" aria-label={playing ? 'Пауза' : 'Проиграть'} onclick={play}>
		{#if playing}<Pause class="size-4" aria-hidden="true" />{:else}<Play class="size-4" aria-hidden="true" />{/if}
	</button>
	<button type="button" class="btn min-h-8 px-2" aria-label="Шаг вперёд" onclick={() => (stop(), onstep(Math.min(max, step + 1)))}>›</button>
	<button type="button" class="btn min-h-8 px-2" aria-label="В конец" onclick={() => (stop(), onstep(max))}>
		<SkipForward class="size-4" aria-hidden="true" />
	</button>
	<input
		type="range"
		class="min-w-32 flex-1 accent-[var(--accent)]"
		min="0"
		{max}
		value={step}
		aria-label="Шаг"
		oninput={(e) => (stop(), onstep(Number(e.currentTarget.value)))}
	/>
	<span class="text-xs tabular-nums text-fg-muted">{step}/{max}</span>
</div>
