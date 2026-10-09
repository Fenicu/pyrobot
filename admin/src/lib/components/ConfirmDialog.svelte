<script lang="ts">
	import { dialogs } from '$lib/stores/confirm.svelte';
	import Modal from './Modal.svelte';

	let value = $state('');
	const req = $derived(dialogs.current);

	$effect(() => {
		if (req) value = '';
	});

	function submit(e: SubmitEvent) {
		e.preventDefault();
		if (req?.input?.required && value.trim() === '') return;
		dialogs.answer(req?.input ? value.trim() : '');
	}
</script>

{#if req}
	<Modal title={req.title} onclose={() => dialogs.answer(null)}>
		<form id="confirm-form" onsubmit={submit} class="space-y-3">
			{#if req.body}
				<p class="ext-text text-sm text-fg-muted">{req.body}</p>
			{/if}
			{#if req.input}
				<label class="block space-y-1">
					<span class="label">{req.input.label}</span>
					<input
						class="input"
						bind:value
						placeholder={req.input.placeholder}
						maxlength={req.input.maxlength}
						required={req.input.required}
						data-autofocus
					/>
				</label>
			{/if}
		</form>
		{#snippet footer()}
			<button type="button" class="btn" onclick={() => dialogs.answer(null)}>
				{req.cancelText ?? 'Отмена'}
			</button>
			{#if req.altText}
				<button type="button" class="btn" onclick={() => dialogs.answerAlt()}>{req.altText}</button>
			{/if}
			<button
				type="submit"
				form="confirm-form"
				class="btn {req.danger ? 'btn-danger' : 'btn-primary'}"
				disabled={req.input?.required && value.trim() === ''}
			>
				{req.confirmText ?? 'Подтвердить'}
			</button>
		{/snippet}
	</Modal>
{/if}
