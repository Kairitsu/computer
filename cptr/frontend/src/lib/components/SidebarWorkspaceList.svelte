<script lang="ts">
	/**
	 * Sidebar chat history: one list, newest first.
	 * Chats that already belong to a folder stay in this list and open back into that folder.
	 */
	import { goto } from '$app/navigation';
	import {
		workspaceList,
		sidebarOpen,
		activeTab,
		activeHomeTab,
		currentWorkspace,
		requestHomeChat,
		openHomeStripChat,
		closeHomeStripChat,
		focusDefaultHomeTab
	} from '$lib/stores';
	import { chatEnabled, updateChatStatuses } from '$lib/stores/chat';
	import { socketStore } from '$lib/stores/socket.svelte';
	import {
		deleteChat as apiDeleteChat,
		getChats,
		updateChatTitle,
		type ChatInfo
	} from '$lib/apis/chat';
	import { t } from '$lib/i18n';
	import { onDestroy, onMount, tick, untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import ChatItem from './common/ChatItem.svelte';
	import DropdownMenu from './DropdownMenu.svelte';
	import Modal from './Modal.svelte';

	const HOME = '';
	const PAGE_SIZE = 50;
	const VISIBLE_STEP = 12;

	type DayBucket = 'today' | 'yesterday' | 'dayBefore' | 'earlier';
	const DAY_ORDER: DayBucket[] = ['today', 'yesterday', 'dayBefore', 'earlier'];

	type ListedChat = ChatInfo & { workspace: string };

	let buckets = $state<Map<string, { chats: ChatInfo[]; hasMore: boolean }>>(new Map());
	let loading = $state(false);
	let loadingMore = $state(false);
	let visibleCount = $state(VISIBLE_STEP);
	let chatMenu = $state<{
		chatId: string;
		workspace: string;
		anchor: HTMLElement;
		step: 'actions' | 'confirm-delete';
	} | null>(null);
	let renameTarget = $state<{ chatId: string; original: string } | null>(null);
	let renameTitle = $state('');
	let renameInput = $state<HTMLInputElement | undefined>();
	let renaming = $state(false);
	let deleting = $state(false);
	let unbindSocketListener: (() => void) | null = null;
	let loadGeneration = 0;

	let currentPath = $derived($currentWorkspace?.path ?? ($activeHomeTab?.workspace || HOME));
	let currentChatId = $derived(
		$currentWorkspace
			? $activeTab?.type === 'chat'
				? $activeTab.path
				: null
			: $activeHomeTab?.type === 'chat' || $activeHomeTab?.type === 'home'
				? ($activeHomeTab.path ?? null)
				: null
	);

	const chats = $derived.by(() => {
		const merged = new Map<string, ListedChat>();
		for (const [path, bucket] of buckets) {
			for (const chat of bucket.chats) {
				const fromMeta = chat.meta?.workspace;
				const workspace = typeof fromMeta === 'string' ? fromMeta : path;
				merged.set(chat.id, { ...chat, workspace });
			}
		}
		return [...merged.values()].sort((a, b) => b.updated_at - a.updated_at);
	});

	const visibleChats = $derived(chats.slice(0, visibleCount));

	const hasMore = $derived([...buckets.values()].some((bucket) => bucket.hasMore));
	const canShowMore = $derived(visibleCount < chats.length || hasMore);

	const dayGroups = $derived.by(() => {
		const labels: Record<DayBucket, string> = {
			today: $t('sidebar.today'),
			yesterday: $t('sidebar.yesterday'),
			dayBefore: $t('sidebar.dayBefore'),
			earlier: $t('sidebar.earlier')
		};
		const grouped = new Map<DayBucket, ListedChat[]>();
		for (const chat of visibleChats) {
			const bucket = dayBucket(chat.updated_at);
			const list = grouped.get(bucket);
			if (list) list.push(chat);
			else grouped.set(bucket, [chat]);
		}
		return DAY_ORDER.filter((bucket) => grouped.has(bucket)).map((bucket) => ({
			bucket,
			label: labels[bucket],
			chats: grouped.get(bucket) ?? []
		}));
	});

	function startOfLocalDay(ts: number): number {
		const date = new Date(ts);
		date.setHours(0, 0, 0, 0);
		return date.getTime();
	}

	function dayBucket(ts: number): DayBucket {
		const days = Math.round((startOfLocalDay(Date.now()) - startOfLocalDay(ts)) / 86_400_000);
		if (days <= 0) return 'today';
		if (days === 1) return 'yesterday';
		if (days === 2) return 'dayBefore';
		return 'earlier';
	}

	function sourcePaths(): string[] {
		const paths = [HOME];
		for (const workspace of $workspaceList) {
			if (workspace.path && !paths.includes(workspace.path)) paths.push(workspace.path);
		}
		return paths;
	}

	async function fetchSource(path: string, offset: number) {
		const data = await getChats(path || undefined, PAGE_SIZE, offset, 'updated_at', 'desc');
		updateChatStatuses(data.chats || [], path);
		return { chats: data.chats || [], hasMore: data.has_more };
	}

	async function loadChats() {
		const generation = ++loadGeneration;
		loading = untrack(() => buckets.size) === 0;
		try {
			const paths = sourcePaths();
			const pages = await Promise.all(
				paths.map(async (path) => {
					try {
						return [path, await fetchSource(path, 0)] as [
							string,
							{ chats: ChatInfo[]; hasMore: boolean }
						];
					} catch {
						return [path, { chats: [], hasMore: false }] as [
							string,
							{ chats: ChatInfo[]; hasMore: boolean }
						];
					}
				})
			);
			if (generation !== loadGeneration) return;
			buckets = new Map(pages);
		} catch {
			if (generation !== loadGeneration) return;
			buckets = new Map();
		} finally {
			if (generation === loadGeneration) loading = false;
		}
	}

	async function loadMore() {
		if (loadingMore) return;
		const nextCount = visibleCount + VISIBLE_STEP;
		if (nextCount > chats.length && hasMore) {
			const pending = [...buckets.entries()].filter(([, bucket]) => bucket.hasMore);
			if (pending.length) {
				loadingMore = true;
				try {
					const pages = await Promise.all(
						pending.map(async ([path, bucket]) => {
							const page = await fetchSource(path, bucket.chats.length);
							const next: { chats: ChatInfo[]; hasMore: boolean } = {
								chats: [...bucket.chats, ...page.chats],
								hasMore: page.hasMore
							};
							return [path, next] as [string, { chats: ChatInfo[]; hasMore: boolean }];
						})
					);
					buckets = new Map([...buckets, ...pages]);
				} catch {
					return;
				} finally {
					loadingMore = false;
				}
			}
		}
		visibleCount = nextCount;
	}

	function closeMobileSidebar() {
		if (typeof window !== 'undefined' && window.innerWidth < 768) sidebarOpen.set(false);
	}

	function openChat(chat: ListedChat) {
		if (chat.workspace) openHomeStripChat(chat.id, chat.workspace);
		else requestHomeChat(chat.id);
		void goto('/');
		closeMobileSidebar();
	}

	function openChatMenu(e: MouseEvent, chat: ListedChat) {
		e.stopPropagation();
		chatMenu = {
			chatId: chat.id,
			workspace: chat.workspace,
			anchor: e.currentTarget as HTMLElement,
			step: 'actions'
		};
	}

	function closeChatMenu() {
		chatMenu = null;
	}

	function askDeleteChat() {
		if (!chatMenu) return;
		chatMenu = { ...chatMenu, step: 'confirm-delete' };
	}

	function listedChat(chatId: string) {
		return chats.find((chat) => chat.id === chatId);
	}

	async function confirmDeleteChat() {
		if (!chatMenu || deleting) return;
		const { chatId, workspace } = chatMenu;
		const title = listedChat(chatId)?.title ?? '';
		deleting = true;
		closeChatMenu();
		try {
			await apiDeleteChat(chatId);
			buckets = new Map(
				[...buckets].map(([path, bucket]) => [
					path,
					{ ...bucket, chats: bucket.chats.filter((chat) => chat.id !== chatId) }
				])
			);
			closeHomeStripChat(chatId);
			if (currentPath === workspace && currentChatId === chatId) {
				if (!workspace) focusDefaultHomeTab();
				void goto('/');
			}
			toast.success($t('chat.history.deleted', { title }));
		} catch {
			toast.error($t('chat.history.deleteFailed'));
		} finally {
			deleting = false;
		}
	}

	function startRename() {
		if (!chatMenu) return;
		const chat = listedChat(chatMenu.chatId);
		renameTarget = { chatId: chatMenu.chatId, original: chat?.title ?? '' };
		renameTitle = chat?.title ?? '';
	}

	function closeRename() {
		if (renaming) return;
		renameTarget = null;
		renameTitle = '';
	}

	async function submitRename() {
		if (!renameTarget || renaming) return;
		const title = renameTitle.trim();
		const { chatId, original } = renameTarget;
		if (!title || title === original) {
			renaming = false;
			renameTarget = null;
			renameTitle = '';
			return;
		}
		renaming = true;
		try {
			await updateChatTitle(chatId, title);
			buckets = new Map(
				[...buckets].map(([path, bucket]) => [
					path,
					{
						...bucket,
						chats: bucket.chats.map((item) => (item.id === chatId ? { ...item, title } : item))
					}
				])
			);
			renameTarget = null;
			renameTitle = '';
		} catch {
			toast.error($t('chat.history.renameFailed'));
		} finally {
			renaming = false;
		}
	}

	$effect(() => {
		if (!renameTarget) return;
		void tick().then(() => {
			renameInput?.focus();
			renameInput?.select();
		});
	});

	const menuItems = $derived.by(() => {
		if (!chatMenu) return [];
		if (chatMenu.step === 'confirm-delete') {
			return [
				{
					label: $t('chat.history.deleteConfirm'),
					icon: 'trash',
					danger: true,
					onclick: (): void => {
						void confirmDeleteChat();
					}
				},
				{
					label: $t('common.cancel'),
					onclick: closeChatMenu
				}
			];
		}
		return [
			...(chatMenu.workspace
				? [
						{
							label: $t('files.copyPath'),
							icon: 'copy',
							onclick: copyChatPath
						}
					]
				: []),
			{
				label: $t('files.rename'),
				icon: 'pencil',
				onclick: startRename
			},
			{
				label: $t('chat.history.delete'),
				icon: 'trash',
				keepOpen: true,
				onclick: askDeleteChat
			}
		];
	});

	function copyChatPath() {
		if (!chatMenu?.workspace) return;
		const chat = listedChat(chatMenu.chatId);
		if (!chat) return;
		const folder = chat.folder ? `${chat.folder}/` : '';
		navigator.clipboard.writeText(
			`${chatMenu.workspace.replace(/\/$/, '')}/.cptr/chats/${folder}${chat.id}.json`
		);
	}

	function handleChatEvent(data: {
		type?: string;
		chat_id: string;
		title?: string;
		workspace?: string;
		active?: boolean;
		updated_at?: number;
		last_read_at?: number;
	}) {
		if (
			!data.title &&
			typeof data.active !== 'boolean' &&
			typeof data.updated_at !== 'number' &&
			typeof data.last_read_at !== 'number'
		) {
			return;
		}

		let known = false;
		buckets = new Map(
			[...buckets].map(([path, bucket]) => [
				path,
				{
					...bucket,
					chats: bucket.chats.map((chat) => {
						if (chat.id !== data.chat_id) return chat;
						known = true;
						return {
							...chat,
							...(data.title ? { title: data.title } : {}),
							...(typeof data.updated_at === 'number' ? { updated_at: data.updated_at } : {}),
							...(typeof data.last_read_at === 'number' ? { last_read_at: data.last_read_at } : {}),
							...(typeof data.active === 'boolean' ? { is_active: data.active } : {})
						};
					})
				}
			])
		);

		if (!known) void loadChats();
	}

	$effect(() => {
		if (!$chatEnabled) return;
		void $workspaceList.map((workspace) => workspace.path).join('\n');
		untrack(() => {
			void loadChats();
		});
	});

	onMount(() => {
		unbindSocketListener = socketStore.on('events:chat', handleChatEvent);
	});

	onDestroy(() => {
		loadGeneration += 1;
		unbindSocketListener?.();
		unbindSocketListener = null;
	});
</script>

<div class="chat-scroll flex-1 min-h-0 overflow-y-auto px-1.5 pb-2">
	{#if $chatEnabled}
		<div class="history-label">{$t('sidebar.history')}</div>
		{#if loading && chats.length === 0}
			<div class="chat-loading">
				<span class="chat-loading-dot"></span>
				<span class="chat-loading-dot"></span>
				<span class="chat-loading-dot"></span>
			</div>
		{:else if chats.length === 0}
			<p class="chat-empty">{$t('sidebar.noChats')}</p>
		{:else}
			{#each dayGroups as group (group.bucket)}
				<div class="day-label">{group.label}</div>
				{#each group.chats as chat (chat.id)}
					<ChatItem
						{chat}
						isSelected={chat.id === currentChatId &&
							(chat.workspace || HOME) === (currentPath || HOME)}
						menuOpen={chatMenu?.chatId === chat.id}
						onclick={() => openChat(chat)}
						onmenu={(e) => openChatMenu(e, chat)}
					/>
				{/each}
			{/each}
			{#if canShowMore}
				<button class="chat-show-more" disabled={loadingMore} onclick={loadMore}>
					{$t('sidebar.showMore')}
				</button>
			{/if}
		{/if}
	{/if}
</div>

{#snippet deleteHint()}
	<p class="delete-hint">
		{$t('chat.history.deleteConsequence', {
			title: listedChat(chatMenu?.chatId ?? '')?.title ?? ''
		})}
	</p>
{/snippet}

{#if chatMenu}
	<DropdownMenu
		anchor={chatMenu.anchor}
		align="end"
		items={menuItems}
		header={chatMenu.step === 'confirm-delete' ? deleteHint : undefined}
		className={chatMenu.step === 'confirm-delete' ? 'min-w-60 w-max max-w-64' : ''}
		onclose={closeChatMenu}
	/>
{/if}

{#if renameTarget}
	<Modal onclose={closeRename} class="mx-4 flex w-full max-w-sm flex-col p-4">
		<h2 class="text-sm font-medium" style="color: var(--app-fg);">{$t('files.rename')}</h2>
		<input
			bind:this={renameInput}
			class="settings-field mt-3 w-full"
			bind:value={renameTitle}
			autocomplete="off"
			spellcheck="false"
			aria-label={$t('files.rename')}
			onkeydown={(e) => {
				if (e.key === 'Enter') {
					e.preventDefault();
					void submitRename();
				}
			}}
		/>
		<div class="mt-4 flex justify-end gap-2">
			<button type="button" class="settings-action" onclick={closeRename}>
				{$t('common.cancel')}
			</button>
			<button
				type="button"
				class="h-7 rounded-lg bg-gray-900 px-3 text-xs font-medium text-white transition-colors duration-75 hover:bg-gray-800 disabled:opacity-40 dark:bg-white dark:text-black dark:hover:bg-white/90"
				disabled={renaming}
				onclick={() => void submitRename()}
			>
				{$t('chat.history.renameConfirm')}
			</button>
		</div>
	</Modal>
{/if}

<style>
	.delete-hint {
		padding: 0.5rem 0.625rem 0.375rem;
		font-size: 0.8125rem;
		font-weight: 500;
		line-height: 1.45;
		color: var(--app-fg);
	}

	.history-label {
		position: sticky;
		top: 0;
		z-index: 2;
		padding: 0.125rem 0.5rem 0.375rem;
		font-size: 0.6875rem;
		font-weight: 600;
		letter-spacing: 0.04em;
		color: var(--app-fg-subtle);
		background: var(--app-sidebar-bg, var(--app-bg));
	}

	.day-label {
		padding: 0.625rem 0.5rem 0.125rem;
		font-size: 0.6875rem;
		font-weight: 500;
		color: var(--app-fg-subtle);
	}

	.chat-empty {
		display: block;
		padding: 0.25rem 0.5rem;
		font-size: 0.75rem;
		color: var(--app-fg-subtle);
	}

	.chat-show-more {
		display: flex;
		align-items: center;
		width: 100%;
		height: 2rem;
		padding: 0 0.5rem;
		border: none;
		background: none;
		font-size: 0.75rem;
		color: var(--app-fg-subtle);
		text-align: left;
		cursor: pointer;
		transition: color 0.1s;
	}

	.chat-show-more:hover {
		color: var(--app-fg);
	}

	.chat-loading {
		display: flex;
		gap: 0.25rem;
		padding: 0.375rem 0.5rem;
	}

	.chat-loading-dot {
		width: 0.25rem;
		height: 0.25rem;
		border-radius: 50%;
		background: var(--app-fg-subtle);
		animation: dotPulse 1s ease-in-out infinite;
	}

	.chat-loading-dot:nth-child(2) {
		animation-delay: 0.15s;
	}

	.chat-loading-dot:nth-child(3) {
		animation-delay: 0.3s;
	}

	@keyframes dotPulse {
		0%,
		100% {
			opacity: 0.3;
		}
		50% {
			opacity: 1;
		}
	}
</style>
