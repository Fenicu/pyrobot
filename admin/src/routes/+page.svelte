<script lang="ts">
	import { goto } from '$app/navigation';
	import { homeHref } from '$lib/nav';

	// «/» — «Обзор»: без аккаунтов он сам покажет ссылку на их список.
	$effect(() => {
		void goto(homeHref(), { replaceState: true });
	});
</script>

<svelte:head><title>pyrobot</title></svelte:head>

<p class="p-6 text-sm text-fg-muted" role="status">Загрузка…</p>
