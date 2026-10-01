<script lang="ts">
	import { accounts } from '$lib/app.svelte';
	import AccountsPending from '$lib/components/AccountsPending.svelte';
</script>

<!-- Старая ссылка (`/journal` и т. п.): ожидание списка аккаунтов; на тот же экран последнего аккаунта
     уводит эффект корневого макета. -->
<AccountsPending error={accounts.list === null ? accounts.error : null} onretry={() => void accounts.load()} />
