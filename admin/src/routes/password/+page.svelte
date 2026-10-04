<script lang="ts">
	import { goto } from '$app/navigation';
	import { call, type Api } from '$lib/api/client';
	import { ApiFailure } from '$lib/api/errors';
	import { api as defaultApi, session as defaultSession } from '$lib/app.svelte';
	import PasswordForm from '$lib/components/PasswordForm.svelte';
	import RecoveryCodes from '$lib/components/auth/RecoveryCodes.svelte';
	import { toasts } from '$lib/stores/toasts.svelte';
	import type { Session } from '$lib/stores/session.svelte';

	interface Props {
		api?: Api;
		session?: Session;
	}
	let { api = defaultApi, session = defaultSession }: Props = $props();

	let confirmPassword = $state('');
	let codes = $state<string[]>([]);
	let reissueError = $state('');
	let reissueBusy = $state(false);

	function done() {
		// Смена пароля отзывает все сессии: токен забывается, дальше — вход.
		session.clear();
		toasts.show('Пароль изменён: все сессии закрыты, войдите снова', 'ok');
		void goto('/login', { replaceState: true });
	}

	async function submitReissue(e: SubmitEvent) {
		e.preventDefault();
		if (!confirmPassword) return;
		reissueBusy = true;
		reissueError = '';
		try {
			const res = await call(api.POST('/api/v1/auth/recovery-codes', { body: { password: confirmPassword } }));
			codes = res.codes;
			confirmPassword = '';
		} catch (err) {
			reissueError = err instanceof ApiFailure ? err.message : String(err);
		} finally {
			reissueBusy = false;
		}
	}
</script>

<svelte:head><title>Пароль и коды · pyrobot</title></svelte:head>

<h1 class="mb-3 text-lg font-semibold">Пароль и коды</h1>
<PasswordForm {api} ondone={done} />

<section class="mt-6 card max-w-sm space-y-3">
	<h2 class="text-base font-semibold">Коды восстановления</h2>
	<p class="text-xs text-fg-muted">
		При перевыпуске прежние коды больше не действуют.
	</p>

	{#if codes.length > 0}
		<RecoveryCodes {codes} />
		<button
			type="button"
			class="btn btn-ghost text-xs"
			onclick={() => {
				codes = [];
				confirmPassword = '';
			}}
		>
			Перевыпустить ещё раз
		</button>
	{:else}
		<form class="space-y-3" onsubmit={submitReissue}>
			<label class="block space-y-1">
				<span class="label">Пароль для подтверждения</span>
				<input
					class="input"
					type="password"
					bind:value={confirmPassword}
					autocomplete="current-password"
					required
					maxlength="1024"
				/>
			</label>
			{#if reissueError}
				<p class="ext-text text-sm text-bad-fg" role="alert">{reissueError}</p>
			{/if}
			<button type="submit" class="btn" disabled={reissueBusy || !confirmPassword}>
				{reissueBusy ? 'Запрос…' : 'Получить новые коды'}
			</button>
		</form>
	{/if}
</section>
