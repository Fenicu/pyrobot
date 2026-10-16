<script lang="ts">
	import { accounts, current } from '$lib/app.svelte';
	import GameView from '$lib/components/game/GameView.svelte';
	import { ManualControl } from '$lib/game/control.svelte';
	import { GameChat } from '$lib/game/store.svelte';

	const ctx = current.get();
	const chat = new GameChat(ctx.api);
	const control = new ManualControl(ctx.api);
	const subscribe = ctx.live.subscribe.bind(ctx.live);

	// Значок «управление у тебя» в списке аккаунтов и статус движка — сразу, а не с опросом.
	function reload() {
		void ctx.engine.load();
		void accounts.load();
	}
</script>

<GameView api={ctx.api} {chat} {control} status={ctx.engine.status} {subscribe} onchange={reload} />
