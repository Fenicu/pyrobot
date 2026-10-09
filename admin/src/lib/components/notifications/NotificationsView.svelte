<script lang="ts">
	import type { AccountApi } from '$lib/api/account';
	import type { LiveEvent } from '$lib/live/sse';
	import Page from '../shell/Page.svelte';
	import Card from '../ui/Card.svelte';
	import NotificationList from './NotificationList.svelte';
	import UnrecognizedList from './UnrecognizedList.svelte';

	interface Props {
		api: AccountApi;
		subscribe?: (handler: (e: LiveEvent) => void) => () => void;
		/** Прочитано на сервере — перечитать счётчик в меню. */
		onread?: () => void;
		now?: Date;
	}
	let { api, subscribe, onread, now }: Props = $props();
	let list = $state<ReturnType<typeof NotificationList>>();
	let unread = $state(0);
	let busy = $state(false);
</script>

{#snippet actions()}
	<button type="button" class="btn" disabled={busy || unread === 0} onclick={() => void list?.readAll()}
		>Прочитать всё</button
	>
{/snippet}

<Page title="Уведомления" {actions}>
	<div class="space-y-[14px]">
		<Card title="Уведомления">
			<NotificationList bind:this={list} bind:unread bind:busy {api} {subscribe} {onread} {now} />
		</Card>
		<Card title="Нераспознанное">
			<UnrecognizedList {api} {now} />
		</Card>
	</div>
</Page>
