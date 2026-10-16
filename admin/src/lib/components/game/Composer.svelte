<script lang="ts">
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import Keyboard from '@lucide/svelte/icons/keyboard';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import SendHorizontal from '@lucide/svelte/icons/send-horizontal';

	interface Props {
		/** Клавиатура игры (последнее входящее с ней); null — её нет. */
		keyboard: string[][] | null;
		/** Почему отправка невозможна; пусто — можно. */
		blocked: string;
		/** Идёт отправка: следующая — после её итога. */
		busy: boolean;
		onsend: (text: string) => void;
	}
	let { keyboard, blocked, busy, onsend }: Props = $props();
	let text = $state('');
	// На телефоне клавиатура сворачивается, на ПК видна всегда.
	let open = $state(true);
	const off = $derived(blocked !== '' || busy);

	function submit(e?: SubmitEvent) {
		e?.preventDefault();
		const command = text.trim();
		if (!command || off) return;
		text = '';
		onsend(command);
	}

	function keydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
			e.preventDefault();
			submit();
		}
	}
</script>

<div class="border-t border-line bg-surface">
	{#if keyboard}
		<button
			type="button"
			class="flex w-full items-center gap-1.5 px-3 pt-1.5 text-xs text-fg-muted md:hidden"
			aria-expanded={open}
			aria-controls="game-keyboard"
			onclick={() => (open = !open)}
		>
			<Keyboard class="size-3.5" aria-hidden="true" />Клавиатура
			<ChevronDown class="ml-auto size-3.5 transition-transform {open ? '' : 'rotate-180'}" aria-hidden="true" />
		</button>
		<div
			id="game-keyboard"
			class="max-h-40 space-y-1 overflow-y-auto px-2 pt-2 {open ? '' : 'max-md:hidden'}"
			role="group"
			aria-label="Клавиатура игры"
		>
			{#each keyboard as row, r (r)}
				<div class="flex gap-1">
					{#each row as key, c (c)}
						<button type="button" class="btn min-h-8 min-w-0 flex-1 text-xs" disabled={off} onclick={() => onsend(key)}>
							<span class="ext-text">{key}</span>
						</button>
					{/each}
				</div>
			{/each}
		</div>
	{/if}
	<form class="flex items-end gap-2 p-2" onsubmit={submit}>
		<label class="min-w-0 flex-1">
			<span class="sr-only">Сообщение в игру</span>
			<textarea
				class="input block max-h-32 resize-none py-1.5 [field-sizing:content]"
				rows="1"
				maxlength="256"
				autocomplete="off"
				placeholder={blocked || 'Команда или текст для игры'}
				disabled={blocked !== ''}
				bind:value={text}
				onkeydown={keydown}
			></textarea>
		</label>
		<button type="submit" class="btn btn-primary shrink-0 px-2.5" disabled={off || !text.trim()} aria-label="Отправить">
			{#if busy}
				<LoaderCircle class="size-4 animate-spin" aria-hidden="true" />
			{:else}
				<SendHorizontal class="size-4" aria-hidden="true" />
			{/if}
		</button>
	</form>
	{#if blocked}<p class="px-3 pb-2 text-xs text-warn-fg">{blocked}</p>{/if}
</div>
