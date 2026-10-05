<script lang="ts">
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	interface Props {
		item: any;
		fallbackId: string;
	}

	let { item, fallbackId }: Props = $props();

	const reasoningId = $derived(item.id || fallbackId);
	const isThinking = $derived(item.status === 'in_progress' || item.status === 'running');
	// Open while the model thinks, so the thought streams in; closed once it is done.
	// A click overrides that for the rest of the block's life.
	let userExpanded = $state<boolean | null>(null);
	const expanded = $derived(userExpanded ?? isThinking);
	const thoughtText = $derived.by(() => {
		return (item.summary ?? item.content ?? [])
			.filter((part: any) => 'text' in part)
			.map((part: any) => part.text ?? '')
			.join('')
			.trim();
	});

	function toggleExpanded() {
		userExpanded = !expanded;
	}

	/** Keep the newest line of a streaming thought in view. */
	function followTail(node: HTMLElement, _text: string) {
		node.scrollTop = node.scrollHeight;
		return {
			update() {
				node.scrollTop = node.scrollHeight;
			}
		};
	}
</script>

<div class="w-full min-w-0 flex flex-col">
	<button
		class="w-full min-w-0 text-left text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition cursor-pointer"
		aria-expanded={expanded}
		aria-controls={reasoningId}
		onclick={toggleExpanded}
	>
		<div class="flex items-center gap-1.5 text-sm min-w-0 {isThinking ? 'shimmer' : ''}">
			<div class="text-gray-400 dark:text-gray-500">
				<svg
					xmlns="http://www.w3.org/2000/svg"
					fill="none"
					viewBox="0 0 24 24"
					stroke-width="1.75"
					stroke="currentColor"
					class="size-3.5"
				>
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						d="M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0 3.09 3.09ZM18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 0 0 2.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 0 0-2.455 2.456ZM16.894 20.567 16.5 21.75l-.394-1.183a2.25 2.25 0 0 0-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 0 0 1.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 0 0 1.423 1.423l1.183.394-1.183.394a2.25 2.25 0 0 0-1.423 1.423Z"
					/>
				</svg>
			</div>

			<div class="flex-1 min-w-0 line-clamp-1">
				<span class="font-normal"> Thinking </span>
			</div>

			<div class="flex shrink-0 self-center translate-y-[0.0625rem]">
				<svg
					xmlns="http://www.w3.org/2000/svg"
					fill="none"
					viewBox="0 0 24 24"
					stroke-width="3.5"
					stroke="currentColor"
					class="size-3 transition-transform duration-200 text-gray-400 dark:text-gray-500 {expanded
						? 'rotate-180'
						: ''}"
				>
					<path stroke-linecap="round" stroke-linejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
				</svg>
			</div>
		</div>
	</button>

	{#if expanded && thoughtText}
		<div id={reasoningId} transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}>
			<div class="mt-1 mb-0.5 px-1">
				<div
					class="text-sm text-gray-500 dark:text-gray-400 whitespace-pre-wrap {isThinking
						? 'max-h-48 overflow-y-auto scrollbar-hover'
						: ''}"
					use:followTail={thoughtText}
				>
					{thoughtText}
				</div>
			</div>
		</div>
	{/if}
</div>
