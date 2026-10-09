<script lang="ts">
	import { onMount } from 'svelte';
	import { beforeNavigate, goto } from '$app/navigation';
	import { accounts, current, session } from '$lib/app.svelte';
	import SettingsView from '$lib/components/settings/SettingsView.svelte';
	import { SettingsEditor } from '$lib/settings/editor.svelte';
	import { leaveGuard } from '$lib/settings/leave';
	import { dialogs } from '$lib/stores/confirm.svelte';

	const { id, api, live, engine } = current.get();
	const editor = new SettingsEditor(api);

	onMount(() => {
		void editor.load();
		return live.subscribe((e) => editor.onEvent(e));
	});

	// Несохранённые правки: уход — после подтверждения. Сессия закончилась — сохранить всё равно
	// нельзя, переход на вход не держим.
	beforeNavigate(
		leaveGuard({
			dirty: () => session.status === 'authenticated' && editor.changes.length > 0,
			confirm: () =>
				dialogs.confirm({
					title: 'Уйти без сохранения?',
					body: `Несохранённых изменений: ${editor.changes.length}. Они пропадут.`,
					confirmText: 'Уйти',
					cancelText: 'Остаться',
					danger: true
				}),
			go: (url, unload) => (unload ? location.assign(url) : void goto(url))
		})
	);
</script>

<SettingsView {api} {editor} running={engine.status?.running !== false} accountId={id} accounts={accounts.list} />
