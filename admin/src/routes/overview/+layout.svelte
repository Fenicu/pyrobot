<script lang="ts">
	import type { Snippet } from 'svelte';
	import { page } from '$app/state';
	import Page from '$lib/components/shell/Page.svelte';

	let { children }: { children: Snippet } = $props();

	const TABS = [
		{ href: '/overview', label: 'Сводка' },
		{ href: '/overview/totals', label: 'Итоги' },
		{ href: '/overview/feed', label: 'Лента' },
		{ href: '/overview/schedule', label: 'Расписание' },
		{ href: '/overview/battle', label: 'Штаб битвы' }
	];
	const path = $derived(page.url.pathname);
	// «Сводка» - сама /overview: остальные подразделы лежат под тем же префиксом.
	const active = (href: string) => (href === '/overview' ? path === href : path === href || path.startsWith(`${href}/`));
	const LINK = 'shrink-0 rounded-md px-2.5 py-1.5 text-sm';
</script>

<Page title="Обзор">
	<div class="mb-3 flex items-center gap-2">
		<nav class="-mx-1 flex min-w-0 flex-1 gap-1 overflow-x-auto px-1" aria-label="Подразделы обзора">
			{#each TABS as tab (tab.href)}
				{@const on = active(tab.href)}
				<a
					href={tab.href}
					class="{LINK} {on ? 'bg-accent-soft font-medium text-accent' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
					aria-current={on ? 'page' : undefined}>{tab.label}</a
				>
			{/each}
		</nav>
		<a href="/accounts" class="shrink-0 text-sm text-fg-muted hover:text-fg">Аккаунты</a>
	</div>
	{@render children()}
</Page>
