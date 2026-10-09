<!-- Оболочка со страницей внутри — как их собирает SvelteKit (для phone.test.ts). -->
<script lang="ts">
	import Shell from '../Shell.svelte';
	import Page from './Page.svelte';

	let { title }: { title: string } = $props();
</script>

<Shell><Page {title}><p>тело</p></Page></Shell>
