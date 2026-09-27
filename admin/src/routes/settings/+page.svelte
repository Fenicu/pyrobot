<script lang="ts">
	import { onMount } from 'svelte';
	import { api, live } from '$lib/app.svelte';
	import SettingsView from '$lib/components/settings/SettingsView.svelte';
	import { SettingsEditor } from '$lib/settings/editor.svelte';

	const editor = new SettingsEditor(api);

	onMount(() => {
		void editor.load();
		return live.subscribe((e) => editor.onEvent(e));
	});
</script>

<svelte:head><title>Настройки · pyrobot</title></svelte:head>

<h1 class="mb-3 text-lg font-semibold">Настройки</h1>
<SettingsView {api} {editor} />
