<script lang="ts">
	import { current } from '$lib/app.svelte';
	import NotificationsView from '$lib/components/notifications/NotificationsView.svelte';

	const { api, live, unread } = current.get();
	const subscribe = live.subscribe.bind(live);
</script>

<NotificationsView {api} {subscribe} onread={() => void unread.load()} />
