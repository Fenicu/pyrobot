<script lang="ts">
	import { parseChangelog } from '$lib/changelog';
	import ChangelogEntry from '$lib/components/changes/ChangelogEntry.svelte';
	import Page from '$lib/components/shell/Page.svelte';
	import { APP_VERSION } from '$lib/stores/whatsnew.svelte';

	const entries = parseChangelog();
</script>

{#snippet status()}
	<span class="pill pill-muted">установлена версия <span class="font-mono">{APP_VERSION}</span></span>
{/snippet}

<Page title="История изменений" {status}>
	<div class="max-w-[72ch] space-y-[14px]">
		{#each entries as entry (entry.version)}
			<section class="card">
				<ChangelogEntry {entry} heading="h2" installed={entry.version === APP_VERSION} />
			</section>
		{/each}
	</div>
</Page>
