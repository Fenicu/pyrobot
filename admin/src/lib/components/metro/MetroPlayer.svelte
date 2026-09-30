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
	}
	let { max, step, onstep }: Props = $props();

	/** Скорость проигрывания: шагов в секунду; ×1 — прежние 8 шагов/с. */
	const SPEEDS = [
		{ label: '×1', perSec: 8 },
		{ label: '×3', perSec: 25 },
		{ label: '×8', perSec: 60 }
	] as const;
	const KEY = 'pyrobot.metro.speed';
	function saved(): number {
		try {
			const n = Number(localStorage.getItem(KEY));
			return SPEEDS.some((s) => s.perSec === n) ? n : SPEEDS[1].perSec;
		} catch {
			return SPEEDS[1].perSec;
		}
	}
	let perSec = $state(saved());

	let timer: ReturnType<typeof setInterval> | null = null;
	let playing = $state(false);
	let current = 0;

	function stop() {
		if (timer !== null) clearInterval(timer);
		timer = null;
		playing = false;
	}

	/** Таймер не чаще 60 раз в секунду: при большей скорости за тик — несколько шагов. */
	function start() {
		const tickMs = Math.max(16, 1000 / perSec);
		const perTick = Math.max(1, Math.round((perSec * tickMs) / 1000));
		timer = setInterval(() => {
			current = Math.min(max, current + perTick);
			onstep(current);
			if (current >= max) stop();
		}, tickMs);
		playing = true;
	}

	function play() {
		if (playing) return stop();
		current = step >= max ? 0 : step;
		onstep(current);
		start();
	}

	function setSpeed(next: number) {
		perSec = next;
		try {
			localStorage.setItem(KEY, String(next));
		} catch {
			// выбор живёт до перезагрузки
		}
		if (playing) {
			if (timer !== null) clearInterval(timer);
			current = step;
			start();
		}
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
	<div class="flex gap-1" role="group" aria-label="Скорость">
		{#each SPEEDS as s (s.perSec)}
			<button
				type="button"
				class="chip min-h-8"
				aria-pressed={perSec === s.perSec}
				title="{s.perSec} шагов в секунду"
				onclick={() => setSpeed(s.perSec)}>{s.label}</button
			>
		{/each}
	</div>
</div>
