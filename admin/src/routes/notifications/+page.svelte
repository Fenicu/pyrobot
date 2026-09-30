<script lang="ts">
	import { accountApi, live, unread } from '$lib/app.svelte';
	import NotificationList from '$lib/components/notifications/NotificationList.svelte';
	import UnrecognizedList from '$lib/components/notifications/UnrecognizedList.svelte';

	let tab = $state<'notifications' | 'unrecognized'>('notifications');
	const subscribe = live.subscribe.bind(live);
</script>

<svelte:head><title>Уведомления · pyrobot</title></svelte:head>

<h1 class="mb-3 text-lg font-semibold">Уведомления</h1>
<div class="mb-3 flex gap-1 border-b border-line" role="tablist" aria-label="Разделы уведомлений">
	<button
		type="button"
		role="tab"
		id="tab-notifications"
		aria-selected={tab === 'notifications'}
		aria-controls="panel"
		class="-mb-px border-b-2 px-3 py-2 text-sm {tab === 'notifications' ? 'border-accent' : 'border-transparent text-fg-muted'}"
		onclick={() => (tab = 'notifications')}>Уведомления</button
	>
	<button
		type="button"
		role="tab"
		id="tab-unrecognized"
		aria-selected={tab === 'unrecognized'}
		aria-controls="panel"
		class="-mb-px border-b-2 px-3 py-2 text-sm {tab === 'unrecognized' ? 'border-accent' : 'border-transparent text-fg-muted'}"
		onclick={() => (tab = 'unrecognized')}>Нераспознанное</button
	>
</div>
<div id="panel" role="tabpanel" aria-labelledby="tab-{tab}">
	{#if tab === 'notifications'}
		<NotificationList api={accountApi} {subscribe} onread={() => void unread.load()} />
	{:else}
		<UnrecognizedList api={accountApi} />
	{/if}
</div>
