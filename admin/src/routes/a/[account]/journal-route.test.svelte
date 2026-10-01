<!-- Макет аккаунта с журналом внутри — как их собирает SvelteKit (для account.test.ts). -->
<script lang="ts">
	import Layout from './+layout.svelte';
	import Journal from './journal/+page.svelte';
</script>

<Layout><Journal /></Layout>
