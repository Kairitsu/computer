<script lang="ts">
	/**
	 * Common chat list item used in sidebar and chat history.
	 * Shows title, relative time, optional spinner for active chats,
	 * and an optional context menu button.
	 */
	import type { ChatInfo } from '$lib/apis/chat';
	import { chatStatuses, isChatUnread } from '$lib/stores/chat';
	import Spinner from './Spinner.svelte';
	import { t } from '$lib/i18n';

	interface Props {
		chat: ChatInfo;
		isSelected?: boolean;
		onclick: () => void;
		/** Optional menu button click handler */
		onmenu?: (e: MouseEvent) => void;
	}
	let { chat, isSelected = false, onclick, onmenu }: Props = $props();
	let status = $derived($chatStatuses.get(chat.id));
	let active = $derived(status?.active ?? chat.is_active ?? false);
	let unread = $derived(
		!isSelected &&
			(status
				? isChatUnread(status)
				: !active && (chat.last_read_at === null || chat.updated_at > chat.last_read_at))
	);

	function formatTime(ts: number): string {
		const diffMin = Math.floor((Date.now() - ts) / 60000);
		if (diffMin < 1) return $t('chat.history.justNow');
		if (diffMin < 60) return $t('chat.history.minutesAgo', { count: diffMin });
		const diffHr = Math.floor(diffMin / 60);
		if (diffHr < 24) return $t('chat.history.hoursAgo', { count: diffHr });
		const diffDay = Math.floor(diffHr / 24);
		if (diffDay < 30) return $t('chat.history.daysAgo', { count: diffDay });
		return new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
	}
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	class="chat-item group/chat flex items-center gap-1.5 w-full h-8 px-2 rounded-lg cursor-pointer transition-colors duration-75"
	class:selected={isSelected}
	role="button"
	tabindex="0"
	{onclick}
	onkeydown={(e) => {
		if (e.key === 'Enter') onclick();
	}}
	title={chat.title}
>
	{#if active}
		<Spinner size={10} borderWidth={1.5} class="opacity-50" />
	{/if}
	{#if unread}
		<span class="size-1.5 shrink-0 rounded-full bg-sky-500" aria-hidden="true"></span>
	{/if}
	<span
		class="flex-1 text-[0.8125rem] truncate min-w-0 {unread || isSelected
			? 'font-medium text-gray-900 dark:text-gray-100'
			: 'text-gray-600 dark:text-gray-400'}">{chat.title}</span
	>
	<span
		class="text-[0.6875rem] text-gray-400 dark:text-gray-600 shrink-0 tabular-nums {onmenu
			? 'group-hover/chat:hidden'
			: ''}">{formatTime(chat.updated_at)}</span
	>
	{#if onmenu}
		<button
			class="hidden group-hover/chat:flex items-center justify-center w-5 h-5 rounded shrink-0 text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-200 dark:hover:bg-white/8 transition-all duration-75"
			onclick={(e) => {
				e.stopPropagation();
				onmenu?.(e);
			}}
			aria-label={$t('a11y.chatOptions')}
		>
			<svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor">
				<circle cx="3" cy="8" r="1.5" />
				<circle cx="8" cy="8" r="1.5" />
				<circle cx="13" cy="8" r="1.5" />
			</svg>
		</button>
	{/if}
</div>

<style>
	.chat-item:hover {
		background: var(--app-hover);
	}

	.chat-item.selected {
		background: color-mix(in oklab, var(--app-accent) 12%, transparent);
	}
</style>
