<script lang="ts">
	import { goto } from '$app/navigation';
	import { call, type Api } from '$lib/api/client';
	import { ApiFailure } from '$lib/api/errors';
	import { accountHref } from '$lib/nav';

	interface Props {
		api: Api;
		store: { load(): Promise<void> };
		/** Аккаунт создан: перед переходом на его экран Telegram. */
		oncreated?: () => void;
		autofocus?: boolean;
	}
	let { api, store, oncreated, autofocus = false }: Props = $props();

	let busy = $state(false);
	let name = $state('');
	let error = $state('');

	async function create(e: SubmitEvent) {
		e.preventDefault();
		error = '';
		busy = true;
		let id: number;
		try {
			id = (await call(api.POST('/api/v1/accounts', { body: { name: name.trim() } }))).id;
		} catch (err) {
			error = err instanceof ApiFailure ? err.message : String(err);
			return;
		} finally {
			busy = false;
		}
		name = '';
		// Макет аккаунта открывает только аккаунт из списка: сначала список, потом переход.
		await store.load();
		oncreated?.();
		await goto(accountHref(id, '/telegram'));
	}
</script>

<form class="space-y-2" onsubmit={create}>
	<div class="flex flex-col gap-2 sm:flex-row sm:items-end">
		<label class="block flex-1 space-y-1">
			<span class="label">Имя нового аккаунта</span>
			<input class="input" bind:value={name} maxlength={64} autocomplete="off" required data-autofocus={autofocus || undefined} />
		</label>
		<button type="submit" class="btn btn-primary" disabled={busy || !name.trim()}>Создать</button>
	</div>
	{#if error}<p class="ext-text text-sm text-warn-fg" role="alert">{error}</p>{/if}
</form>
