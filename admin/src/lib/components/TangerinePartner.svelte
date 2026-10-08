<script lang="ts">
	import type { AccountApi } from '$lib/api/account';
	import { call } from '$lib/api/client';
	import type { TangerinePartnerOut } from '$lib/api/types';
	import { partnerLine } from '$lib/tangerine/partner';

	interface Props {
		api: AccountApi;
		/** Смена значения — перечитать адресата (адресат или версия настроек). */
		refresh?: unknown;
	}
	let { api, refresh }: Props = $props();

	let out = $state<TangerinePartnerOut | null>(null);
	let seq = 0;
	const line = $derived(partnerLine(out));

	async function load() {
		const mine = ++seq;
		try {
			const next = await call(api.GET('/tangerine/partner'));
			if (mine === seq) out = next;
		} catch {
			if (mine === seq) out = null;
		}
	}

	$effect(() => {
		void refresh;
		void load();
	});
</script>

{#if line}
	<p class="text-sm {line.bad ? 'text-bad-fg' : ''}" role="status">
		{#if line.href}<a class="text-accent hover:underline" href={line.href}>{line.text}</a>{:else}{line.text}{/if}
	</p>
{/if}
