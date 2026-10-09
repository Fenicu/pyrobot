<script lang="ts">
	import type { AccountApi } from '$lib/api/account';
	import type { SettingsEditor } from '$lib/settings/editor.svelte';
	import Modal from '../Modal.svelte';
	import SettingsHistory from './SettingsHistory.svelte';

	interface Props {
		api: AccountApi;
		editor: SettingsEditor;
		refresh: number;
		now?: Date;
		onclose: () => void;
	}
	let { api, editor, refresh, now, onclose }: Props = $props();
</script>

<!-- На ПК — панель справа, на телефоне — шторка снизу. -->
<Modal title="История" variant="sheet" {onclose}>
	<SettingsHistory {api} {editor} {refresh} {now} />
</Modal>
