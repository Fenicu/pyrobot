<script lang="ts">
	import type { BulkGroup, BulkSummary } from '$lib/overview/bulk';
	import Modal from '../Modal.svelte';

	interface Props {
		title: string;
		summary: BulkSummary;
		/** Имя аккаунта по id (id нет в списке - «#id»). */
		nameOf: (id: number) => string;
		onclose: () => void;
	}
	let { title, summary, nameOf, onclose }: Props = $props();

	const GROUPS: { key: BulkGroup; label: string; tone: string }[] = [
		{ key: 'applied', label: 'Применено', tone: 'text-ok-fg' },
		{ key: 'unchanged', label: 'Не изменилось', tone: 'text-fg-muted' },
		{ key: 'skipped', label: 'Пропущено', tone: 'text-warn-fg' },
		{ key: 'error', label: 'Ошибка', tone: 'text-bad-fg' }
	];
</script>

<Modal {title} {onclose}>
	{#if summary.rows.length === 0}
		<p class="text-sm text-fg-muted">Нет аккаунтов, к которым можно применить действие.</p>
	{/if}
	<div class="space-y-3">
		{#each GROUPS as group (group.key)}
			{@const rows = summary.rows.filter((r) => r.group === group.key)}
			{#if rows.length > 0}
				<section aria-label={group.label}>
					<h3 class="text-sm font-medium {group.tone}">{group.label}: {rows.length}</h3>
					<ul class="mt-1 space-y-0.5 text-sm">
						{#each rows as row (row.accountId)}
							<li class="ext-text"><span class="font-medium">{nameOf(row.accountId)}</span>{' '}<span class="text-fg-muted">- {row.text}</span></li>
						{/each}
					</ul>
				</section>
			{/if}
		{/each}
	</div>
	{#snippet footer()}
		<button type="button" class="btn btn-primary" data-autofocus onclick={onclose}>Закрыть</button>
	{/snippet}
</Modal>
