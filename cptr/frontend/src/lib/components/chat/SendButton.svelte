<script lang="ts">
	interface Props {
		canSend: boolean;
		streaming: boolean;
		onsend: () => void;
		oncancel?: () => void;
	}
	let { canSend, streaming, onsend, oncancel }: Props = $props();

	// Show send when there's sendable text, even during streaming (enqueue).
	// Show stop only when streaming with nothing to send.
	const showStop = $derived(streaming && !canSend && !!oncancel);
</script>

{#if showStop}
	<button
		class="bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-white hover:bg-gray-200 dark:hover:bg-gray-600 transition rounded-full p-1"
		onclick={oncancel}
	>
		<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" class="size-4">
			<path
				fill-rule="evenodd"
				d="M2.25 12c0-5.385 4.365-9.75 9.75-9.75s9.75 4.365 9.75 9.75-4.365 9.75-9.75 9.75S2.25 17.385 2.25 12zm6-2.438c0-.724.588-1.312 1.313-1.312h4.874c.725 0 1.313.588 1.313 1.313v4.874c0 .725-.588 1.313-1.313 1.313H9.564a1.312 1.312 0 01-1.313-1.313V9.564z"
				clip-rule="evenodd"
			/>
		</svg>
	</button>
{:else}
	<button
		class="{canSend
			? 'send-ready hover:opacity-90'
			: 'text-white bg-gray-200 dark:text-gray-900 dark:bg-gray-700 cursor-default'} transition rounded-full p-1 self-center"
		onclick={onsend}
		disabled={!canSend}
	>
		<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="size-4">
			<path
				fill-rule="evenodd"
				d="M8 14a.75.75 0 0 1-.75-.75V4.56L4.03 7.78a.75.75 0 0 1-1.06-1.06l4.5-4.5a.75.75 0 0 1 1.06 0l4.5 4.5a.75.75 0 0 1-1.06 1.06L8.75 4.56v8.69A.75.75 0 0 1 8 14Z"
				clip-rule="evenodd"
			/>
		</svg>
	</button>
{/if}

<style>
	.send-ready {
		background: var(--app-accent);
		color: var(--app-accent-fg);
	}
</style>
