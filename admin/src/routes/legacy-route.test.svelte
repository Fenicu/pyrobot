<!-- Корневой макет со страницей старой ссылки — как их собирает SvelteKit (для legacy.test.ts). -->
<script lang="ts">
	import Layout from './+layout.svelte';
	import Legacy from './[legacy=legacy]/+page.svelte';
</script>

<Layout><Legacy /></Layout>
