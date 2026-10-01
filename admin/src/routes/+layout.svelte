<script lang="ts">
	import '../app.css';
	import type { Snippet } from 'svelte';
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { accounts, session, startApp, stopApp } from '$lib/app.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import Shell from '$lib/components/Shell.svelte';
	import Toasts from '$lib/components/Toasts.svelte';
	import { isLegacy, lastAccount, legacyHref, loginHref, pickAccount, safeNext } from '$lib/nav';
	import { theme } from '$lib/stores/theme.svelte';

	let { children }: { children: Snippet } = $props();
	const onLogin = $derived(page.url.pathname === '/login');
	const legacy = $derived(isLegacy(page.url.pathname));

	onMount(() => {
		theme.init();
		void session.start();
		return () => {
			session.stop();
			stopApp();
		};
	});

	// Вход открывает список аккаунтов и возвращает на исходную страницу; выход или 401 — закрывает
	// аккаунт и ведёт на /login с возвратом. Сбой связи при старте на вход не ведёт.
	$effect(() => {
		if (session.status === 'authenticated') {
			startApp();
			if (onLogin) void goto(safeNext(page.url.searchParams.get('next')), { replaceState: true });
		} else if (session.status === 'anonymous') {
			stopApp();
			if (!onLogin) void goto(loginHref(page.url), { replaceState: true });
		}
	});

	// Старые ссылки (`/journal` и т. п.; маршрут `[legacy=legacy]` — ожидание) — тот же экран
	// последнего аккаунта.
	$effect(() => {
		if (session.status !== 'authenticated' || !legacy || accounts.list === null) return;
		const href = legacyHref(page.url, pickAccount(accounts.list, lastAccount()));
		if (href !== null) void goto(href, { replaceState: true });
	});
</script>

{#if onLogin}
	{@render children()}
{:else if session.status === 'authenticated' && legacy}
	{@render children()}
{:else if session.status === 'authenticated'}
	<Shell>{@render children()}</Shell>
{:else if session.offline}
	<main class="flex min-h-dvh items-center justify-center p-4">
		<section class="card w-full max-w-sm space-y-3 p-5 text-sm" role="alert">
			<p class="font-medium">Нет связи с сервером</p>
			<p class="text-fg-muted">Повтор через {Math.round(session.retryIn / 1000)} с.</p>
			<button type="button" class="btn btn-primary w-full" onclick={() => session.retry()}>Повторить</button>
		</section>
	</main>
{:else}
	<p class="p-6 text-sm text-fg-muted" role="status">Загрузка…</p>
{/if}
<ConfirmDialog />
<Toasts />
