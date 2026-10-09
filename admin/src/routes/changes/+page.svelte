<script lang="ts">
	import { parseChangelog } from '$lib/changelog';
	import ChangelogEntry from '$lib/components/changes/ChangelogEntry.svelte';
	import Page from '$lib/components/shell/Page.svelte';
	import { APP_VERSION } from '$lib/stores/whatsnew.svelte';

	const entries = parseChangelog();
</script>

<Page title="История изменений">
	<p class="mb-3 text-sm text-fg-muted">Установлена версия <span class="font-mono">{APP_VERSION}</span></p>
	<div class="max-w-3xl space-y-3">
		{#each entries as entry (entry.version)}
			<section class="card">
				<ChangelogEntry {entry} heading="h2" installed={entry.version === APP_VERSION} />
			</section>
		{/each}
	</div>
</Page>
