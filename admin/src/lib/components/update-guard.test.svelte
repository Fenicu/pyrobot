<!-- Плашка новой версии и страница с черновиком; порядок регистрации обработчиков в SvelteKit —
     порядок onMount и зависит от того, что смонтировано раньше. -->
<script lang="ts">
	import Guard from './leave-guard.test.svelte';
	import UpdateBanner from './UpdateBanner.svelte';

	interface Props {
		guardFirst: boolean;
		confirm: () => Promise<boolean>;
		go: (url: URL) => void;
	}
	let { guardFirst, confirm, go }: Props = $props();
</script>

{#if guardFirst}
	<Guard {confirm} {go} />
	<UpdateBanner />
{:else}
	<UpdateBanner />
	<Guard {confirm} {go} />
{/if}
