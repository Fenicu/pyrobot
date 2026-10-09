<script lang="ts">
	import Eye from '@lucide/svelte/icons/eye';
	import { BLOCK_TITLES } from './blocks';
	import type { HomeLayoutStore } from './store.svelte';

	interface Props {
		store: HomeLayoutStore;
		/** «Готово»: сохранить и выйти из режима (ошибку показывает страница). */
		onsave: () => void;
	}
	let { store, onsave }: Props = $props();
	const hidden = $derived(store.draft?.hidden ?? []);
</script>

<div
	class="mb-3.5 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-card border border-accent bg-accent-soft px-3.5 py-2.5"
	role="region"
	aria-label="Настройка главной"
>
	<p class="text-sm">
		<span class="font-medium">Настройка главной.</span>
		<span class="text-fg-muted">Блоки перетаскиваются за заголовок, размер — за правый нижний угол.</span>
	</p>
	{#if hidden.length > 0}
		<div class="flex flex-wrap items-center gap-1.5" role="group" aria-label="Скрытые блоки">
			<span class="text-xs text-fg-muted">Скрыты:</span>
			{#each hidden as id (id)}
				<button
					type="button"
					class="chip"
					aria-label="Вернуть «{BLOCK_TITLES[id]}»"
					disabled={store.saving}
					onclick={() => store.restore(id)}
				>
					<Eye class="size-3.5" aria-hidden="true" />
					{BLOCK_TITLES[id]} · вернуть
				</button>
			{/each}
		</div>
	{/if}
	<div class="ml-auto flex flex-wrap items-center gap-2">
		<button type="button" class="btn" disabled={store.saving} onclick={() => store.reset()}>
			Сбросить по умолчанию
		</button>
		<button type="button" class="btn" disabled={store.saving} onclick={() => store.cancel()}>Отмена</button>
		<button type="button" class="btn btn-primary" disabled={store.saving} onclick={onsave}>
			{store.saving ? 'Сохранение…' : 'Готово'}
		</button>
	</div>
</div>
