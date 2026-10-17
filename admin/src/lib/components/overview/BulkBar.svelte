<script lang="ts">
	interface Props {
		count: number;
		busy: boolean;
		onpause: () => void;
		onresume: () => void;
		onsettings: () => void;
		onclear: () => void;
	}
	let { count, busy, onpause, onresume, onsettings, onclear }: Props = $props();
</script>

<div class="card sticky bottom-3 z-20 flex flex-wrap items-center gap-2 shadow-lg" role="region" aria-label="Действия с выбранными">
	<span class="mr-1 text-sm font-medium">Выбрано {count}:</span>
	<button type="button" class="btn" disabled={busy} onclick={onpause}>Пауза</button>
	<button type="button" class="btn" disabled={busy} onclick={onresume}>Продолжить</button>
	<button type="button" class="btn" disabled={busy} onclick={onsettings}>Настройка…</button>
	<button type="button" class="btn btn-ghost ml-auto" onclick={onclear}>Снять выбор</button>
</div>
