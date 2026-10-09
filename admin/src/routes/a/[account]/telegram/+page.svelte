<script lang="ts">
	import { current } from '$lib/app.svelte';
	import TelegramView from '$lib/components/telegram/TelegramView.svelte';

	const { api, engine } = current.get();
</script>

<TelegramView {api} {engine} />
