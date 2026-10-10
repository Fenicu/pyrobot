<!-- Страница с несохранённым черновиком: leaveGuard, как у настроек. -->
<script lang="ts">
	import { beforeNavigate } from '$app/navigation';
	import { leaveGuard } from '$lib/settings/leave';

	let { confirm, go }: { confirm: () => Promise<boolean>; go: (url: URL) => void } = $props();

	beforeNavigate(leaveGuard({ dirty: () => true, confirm: () => confirm(), go: (url) => go(url) }));
</script>
