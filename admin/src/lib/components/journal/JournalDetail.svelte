<script lang="ts">
	import type { AccountApi } from '$lib/api/account';
	import type { JournalItem } from '$lib/api/types';
	import type { Confirmer } from '$lib/commands';
	import type { LiveEvent } from '$lib/live/sse';
	import ActionDetail from './ActionDetail.svelte';
	import DecisionDetail from './DecisionDetail.svelte';
	import MessageDetail from './MessageDetail.svelte';

	interface Props {
		api: AccountApi;
		item: JournalItem;
		subscribe?: (handler: (e: LiveEvent) => void) => () => void;
		onstale?: () => void;
		confirmer?: Confirmer;
	}
	let { api, item, subscribe, onstale, confirmer }: Props = $props();
</script>

{#if item.type === 'decision'}
	<DecisionDetail {api} id={item.id} {subscribe} />
{:else if item.type === 'action'}
	<ActionDetail {api} id={item.id} {subscribe} />
{:else}
	<MessageDetail {api} {item} {onstale} {confirmer} />
{/if}
