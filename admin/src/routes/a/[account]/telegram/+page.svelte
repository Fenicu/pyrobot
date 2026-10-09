<script lang="ts">
	import { current } from '$lib/app.svelte';
	import Page from '$lib/components/shell/Page.svelte';
	import TelegramView from '$lib/components/telegram/TelegramView.svelte';

	const { api, engine } = current.get();
</script>

<Page title="Telegram">
	<TelegramView {api} {engine} />
</Page>
