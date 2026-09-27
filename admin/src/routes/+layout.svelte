<script lang="ts">
	import '../app.css';
	import type { Snippet } from 'svelte';
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { session, startApp, stopApp } from '$lib/app.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import Shell from '$lib/components/Shell.svelte';
	import Toasts from '$lib/components/Toasts.svelte';
	import { theme } from '$lib/stores/theme.svelte';

	let { children }: { children: Snippet } = $props();
	const onLogin = $derived(page.url.pathname === '/login');

	onMount(() => {
		theme.init();
		void session.load();
		return stopApp;
	});

	// Вход открывает поток и счётчики, выход или 401 — закрывает и ведёт на /login.
	$effect(() => {
		if (session.status === 'authenticated') {
			startApp();
			if (onLogin) void goto('/', { replaceState: true });
		} else if (session.status === 'anonymous') {
			stopApp();
			if (!onLogin) void goto('/login', { replaceState: true });
		}
	});
</script>

{#if onLogin}
	{@render children()}
{:else if session.status === 'authenticated'}
	<Shell>{@render children()}</Shell>
{:else}
	<p class="p-6 text-sm text-fg-muted" role="status">Загрузка…</p>
{/if}
<ConfirmDialog />
<Toasts />
