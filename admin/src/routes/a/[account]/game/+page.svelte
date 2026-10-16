<script lang="ts">
	import { current } from '$lib/app.svelte';
	import GameView from '$lib/components/game/GameView.svelte';
	import { GameChat } from '$lib/game/store.svelte';

	const ctx = current.get();
	const chat = new GameChat(ctx.api);
	const subscribe = ctx.live.subscribe.bind(ctx.live);
</script>

<GameView api={ctx.api} {chat} status={ctx.engine.status} {subscribe} />
