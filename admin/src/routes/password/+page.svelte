<script lang="ts">
	import { goto } from '$app/navigation';
	import { api, session } from '$lib/app.svelte';
	import PasswordForm from '$lib/components/PasswordForm.svelte';
	import { toasts } from '$lib/stores/toasts.svelte';

	function done() {
		// Смена пароля отзывает все сессии: токен забывается, дальше — вход.
		session.clear();
		toasts.show('Пароль изменён: все сессии закрыты, войдите снова', 'ok');
		void goto('/login', { replaceState: true });
	}
</script>

<svelte:head><title>Смена пароля · pyrobot</title></svelte:head>

<h1 class="mb-3 text-lg font-semibold">Смена пароля</h1>
<PasswordForm {api} ondone={done} />
