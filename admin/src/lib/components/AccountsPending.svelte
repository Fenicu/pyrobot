<script lang="ts">
	import { errorText, type ApiError } from '$lib/api/errors';

	interface Props {
		/** Ошибка чтения списка аккаунтов (null — ещё грузится). */
		error?: ApiError | null;
		onretry?: () => void;
	}
	let { error = null, onretry }: Props = $props();
</script>

<!-- Пока нет списка аккаунтов: экран аккаунта и переходы по старым ссылкам ждут его. -->
{#if error}
	<section class="card mx-auto max-w-sm space-y-3 text-sm" role="alert">
		<p class="font-medium">Список аккаунтов недоступен: {errorText(error)}.</p>
		{#if onretry}
			<button type="button" class="btn btn-primary w-full" onclick={onretry}>Повторить</button>
		{/if}
	</section>
{:else}
	<p class="p-6 text-sm text-fg-muted" role="status">Загрузка…</p>
{/if}
