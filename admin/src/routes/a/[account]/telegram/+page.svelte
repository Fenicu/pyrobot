<script lang="ts">
	import { current } from '$lib/app.svelte';
	import TelegramView from '$lib/components/telegram/TelegramView.svelte';

	const { api } = current.get();
</script>

<svelte:head><title>Telegram · pyrobot</title></svelte:head>

<h1 class="mb-3 text-lg font-semibold">Telegram</h1>
<TelegramView {api} />
