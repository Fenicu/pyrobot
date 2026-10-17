<!-- Макет «Обзора» с вложенной страницей — как их собирает SvelteKit (для layout.test.ts). -->
<script lang="ts">
	import Layout from './+layout.svelte';
	import Summary from './+page.svelte';
</script>

<Layout><Summary /></Layout>
