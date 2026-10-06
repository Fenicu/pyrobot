<script lang="ts">
	import type { ChangelogEntry as Entry } from '$lib/changelog';
	import Modal from '../Modal.svelte';
	import ChangelogEntry from './ChangelogEntry.svelte';

	let { entries, onclose }: { entries: Entry[]; onclose: () => void } = $props();
</script>

<Modal title="Что нового" {onclose}>
	<div class="space-y-4">
		{#each entries as entry (entry.version)}
			<section><ChangelogEntry {entry} /></section>
		{/each}
	</div>
	{#snippet footer()}
		<a href="/changes" class="btn btn-ghost mr-auto" onclick={onclose}>Вся история изменений</a>
		<button type="button" class="btn btn-primary" onclick={onclose} data-autofocus>Понятно</button>
	{/snippet}
</Modal>
