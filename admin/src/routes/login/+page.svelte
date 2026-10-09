<script lang="ts">
	import { errorText } from '$lib/api/errors';
	import { session } from '$lib/app.svelte';
	import AuthHeader from '$lib/components/auth/AuthHeader.svelte';

	let login = $state('admin');
	let password = $state('');
	let error = $state('');
	let busy = $state(false);

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		error = '';
		const failure = await session.signIn(login.trim(), password);
		busy = false;
		password = '';
		if (failure) {
			error = failure.kind === 'unauthorized' ? 'Неверный логин или пароль' : errorText(failure);
		}
	}
</script>

<svelte:head><title>Вход · pyrobot</title></svelte:head>

<main class="flex min-h-dvh items-center justify-center p-4">
	<form class="card w-full max-w-sm space-y-4 p-6" onsubmit={submit}>
		<AuthHeader subtitle="Вход в админку" />
		<label class="block space-y-1">
			<span class="label">Логин</span>
			<input class="input" bind:value={login} autocomplete="username" required maxlength="64" />
		</label>
		<label class="block space-y-1">
			<span class="label">Пароль</span>
			<input
				class="input"
				type="password"
				bind:value={password}
				autocomplete="current-password"
				required
				maxlength="1024"
			/>
		</label>
		<div class="flex items-center justify-end">
			<a href="/recover" class="text-xs text-fg-muted hover:text-fg">Забыли пароль?</a>
		</div>
		{#if error}
			<p class="ext-text text-sm text-bad-fg" role="alert">{error}</p>
		{/if}
		<button type="submit" class="btn btn-primary w-full" disabled={busy}>
			{busy ? 'Вход…' : 'Войти'}
		</button>
	</form>
</main>
