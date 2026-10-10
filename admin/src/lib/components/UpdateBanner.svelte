<script lang="ts">
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import { onNavigate } from '$app/navigation';
	import { updated } from '$app/state';

	// Новая версия — переход на другую страницу полной загрузкой. onNavigate, а не beforeNavigate:
	// он вызывается, только если переход никто не отменил (уход с несохранёнными правками ждёт
	// подтверждения). Смена query на той же странице (вкладка, фильтр) черновик не сбрасывает.
	onNavigate(({ from, to }) => {
		if (updated.current && to !== null && from?.url.pathname !== to.url.pathname) location.href = to.url.href;
	});
</script>

<!-- Таймер опроса в свёрнутом приложении на телефоне стоит: проверка — при возврате на экран. -->
<svelte:document
	onvisibilitychange={() => {
		if (document.visibilityState === 'visible' && !updated.current) void updated.check();
	}}
/>

{#if updated.current}
	<div
		class="fixed inset-x-2 top-[calc(0.5rem_+_env(safe-area-inset-top))] z-[60] mx-auto flex max-w-md items-center gap-3 rounded-lg border border-accent bg-surface px-3 py-2 text-sm shadow-lg"
		role="status"
	>
		<span class="flex-1 font-medium">Вышла новая версия</span>
		<button type="button" class="btn btn-primary min-h-8" onclick={() => location.reload()}>
			<RefreshCw class="size-4" aria-hidden="true" />Обновить
		</button>
	</div>
{/if}
