<script lang="ts">
	import DropdownMenu from '../DropdownMenu.svelte';
	import type { ContextUsage } from '$lib/apis/chat';
	import { t } from '$lib/i18n';

	interface Props {
		chatId: string | null;
		contextUsage: ContextUsage | null;
		queuedMessages?: { id: string; content: string }[];
		anchor: HTMLElement | { x: number; y: number };
		onclose: () => void;
	}

	let { chatId, contextUsage, queuedMessages = [], anchor, onclose }: Props = $props();

	let copied = $state(false);
	const contextPercent = $derived(Math.max(0, Math.round(contextUsage?.percent ?? 0)));
	const contextValue = $derived(
		contextUsage
			? `${contextPercent}% ${formatTokenCount(contextUsage.estimated_tokens || contextUsage.tokens)}/${formatTokenCount(contextUsage.threshold)}`
			: $t('chat.statusUnknown')
	);
	const contextBarPercent = $derived(Math.min(contextPercent, 100));

	function copyChatId() {
		if (!chatId) return;
		navigator.clipboard.writeText(chatId);
		copied = true;
		setTimeout(() => (copied = false), 1600);
	}

	function formatTokenCount(value: number): string {
		if (value >= 1_000_000) return `${trimNumber(value / 1_000_000)}m`;
		if (value >= 1_000) return `${trimNumber(value / 1_000)}k`;
		return String(value);
	}

	function trimNumber(value: number): string {
		return value >= 10 ? String(Math.round(value)) : value.toFixed(1).replace(/\.0$/, '');
	}
</script>

<DropdownMenu
	items={[]}
	{anchor}
	{onclose}
	align="end"
	className="w-80 max-w-[calc(100vw-1.5rem)] p-0"
>
	<div class="max-h-[70dvh] overflow-y-auto px-1 py-0.5 text-xs sm:max-h-[min(72dvh,32.5rem)]">
		<section>
			<div class="rounded-xl px-1.5 py-1.5 text-gray-600 dark:text-gray-400">
				<div class="flex h-5 items-center gap-3">
					<span class="min-w-0 flex-1 truncate">{$t('chat.statusContextUsage')}</span>
					<span class="shrink-0 font-mono text-[0.625rem] text-gray-400 dark:text-gray-600">
						{contextValue}
					</span>
				</div>
				<div class="mt-2 h-px overflow-hidden rounded-full bg-gray-100 dark:bg-white/8">
					<div
						class="h-full rounded-full bg-gray-300 dark:bg-white/20"
						style={`width: ${contextBarPercent}%`}
					></div>
				</div>
			</div>

			{#if queuedMessages.length}
				<div class="flex h-7 items-center gap-3 rounded-xl px-1.5 text-gray-600 dark:text-gray-400">
					<span class="min-w-0 flex-1 truncate">{$t('chat.queuedMessages')}</span>
					<span class="font-mono text-[0.625rem] text-gray-400 dark:text-gray-600">
						{queuedMessages.length}
					</span>
				</div>
			{/if}

			{#if chatId}
				<div class="flex h-7 items-center gap-3 rounded-xl px-1.5 text-gray-600 dark:text-gray-400">
					<span class="min-w-0 flex-1 truncate">{$t('chat.statusChatId')}</span>
					<button
						type="button"
						class="min-w-0 max-w-[11rem] truncate font-mono text-[0.625rem] text-gray-400 underline-offset-2 transition-colors duration-75 hover:text-gray-700 hover:underline dark:text-gray-600 dark:hover:text-gray-200"
						onclick={copyChatId}
					>
						{copied ? $t('about.copied') : chatId}
					</button>
				</div>
			{:else}
				<div class="flex h-7 items-center gap-3 rounded-xl px-1.5 text-gray-600 dark:text-gray-400">
					<span class="min-w-0 flex-1 truncate">{$t('chat.statusChatId')}</span>
					<span class="font-mono text-[0.625rem] text-gray-400 dark:text-gray-600">
						{$t('chat.statusNoChat')}
					</span>
				</div>
			{/if}
		</section>
	</div>
</DropdownMenu>
