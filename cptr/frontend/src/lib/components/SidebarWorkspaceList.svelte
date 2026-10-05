<script lang="ts">
	/**
	 * Sidebar chat history, grouped like Grok App:
	 *   项目 / Projects        each workspace with its latest chats (expanded by default)
	 *   默认工作区 / Default   Home chats that belong to no workspace
	 */
	import { goto } from '$app/navigation';
	import {
		workspaceList,
		removeWorkspace,
		reorderWorkspaces,
		sidebarOpen,
		activeTab,
		activeHomeTab,
		currentWorkspace,
		requestHomeChat
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
	import { tooltip } from '$lib/tooltip';
	import Sortable from 'sortablejs';
	import { onDestroy, onMount } from 'svelte';
	import ChatItem from './common/ChatItem.svelte';
	import DropdownMenu from './DropdownMenu.svelte';
	import Icon from './Icon.svelte';

	interface Props {
		onaddworkspace: () => void;
	}

	let { onaddworkspace }: Props = $props();

	/** Cache key for Home chats (no workspace). */
	const HOME = '';
	const PROJECTS_KEY = '__projects__';
	const COLLAPSED_STORAGE_KEY = 'cptr:sidebar:collapsed';
	const WS_CHATS_PAGE_SIZE = 5;

	let wsMenuPath = $state<string | null>(null);
	let wsMenuAnchor = $state<HTMLElement | null>(null);
	let chatMenu = $state<{ chatId: string; wsPath: string; anchor: HTMLElement } | null>(null);
	let wsListEl: HTMLDivElement | undefined = $state();
	let sortable: Sortable | null = null;
	let unbindSocketListener: (() => void) | null = null;

	// Collapsed sections/workspaces; everything starts expanded.
	let collapsed = $state<Set<string>>(loadCollapsed());
	let wsChatsCache = $state<Map<string, ChatInfo[]>>(new Map());
	let wsChatsHasMore = $state<Map<string, boolean>>(new Map());
	let wsChatsLoading = $state<Set<string>>(new Set());
	let currentPath = $derived($currentWorkspace?.path ?? HOME);
	let currentChatId = $derived(
		$currentWorkspace
			? $activeTab?.type === 'chat'
				? $activeTab.path
				: null
			: $activeHomeTab?.type === 'chat' || $activeHomeTab?.type === 'home'
				? ($activeHomeTab.path ?? null)
				: null
	);
	const projectsExpanded = $derived(!collapsed.has(PROJECTS_KEY));
	const homeExpanded = $derived(!collapsed.has(HOME));

	function loadCollapsed(): Set<string> {
		try {
			const raw = localStorage.getItem(COLLAPSED_STORAGE_KEY);
			const parsed = raw ? JSON.parse(raw) : [];
			return new Set(Array.isArray(parsed) ? parsed.filter((v) => typeof v === 'string') : []);
		} catch {
			return new Set();
		}
	}

	function isExpanded(key: string) {
		return !collapsed.has(key);
	}

	function toggleCollapsed(key: string) {
		const next = new Set(collapsed);
		if (next.has(key)) next.delete(key);
		else next.add(key);
		collapsed = next;
		try {
			localStorage.setItem(COLLAPSED_STORAGE_KEY, JSON.stringify([...next]));
		} catch {
			// Storage can be unavailable (private mode); collapse state is a convenience.
		}
		if (!next.has(key) && key !== PROJECTS_KEY && !wsChatsCache.has(key)) {
			void fetchWorkspaceChats(key);
		}
	}

	function sortChats(chats: ChatInfo[]) {
		return chats.sort(
			(a, b) =>
				Number(!b.is_active && (b.last_read_at === null || b.updated_at > b.last_read_at)) -
					Number(!a.is_active && (a.last_read_at === null || a.updated_at > a.last_read_at)) ||
				b.updated_at - a.updated_at
		);
	}

	async function fetchWorkspaceChats(path: string, append = false, limit = WS_CHATS_PAGE_SIZE) {
		if (wsChatsLoading.has(path)) return;
		wsChatsLoading = new Set([...wsChatsLoading, path]);
		try {
			const existing = wsChatsCache.get(path) ?? [];
			const data = await getChats(
				path || undefined,
				append ? WS_CHATS_PAGE_SIZE : limit,
				append ? existing.length : 0,
				'updated_at',
				'desc'
			);
			wsChatsCache = new Map([
				...wsChatsCache,
				[path, append ? sortChats([...existing, ...(data.chats || [])]) : data.chats || []]
			]);
			updateChatStatuses(data.chats || [], path);
			wsChatsHasMore = new Map([...wsChatsHasMore, [path, data.has_more]]);
		} catch {
			wsChatsCache = new Map([...wsChatsCache, [path, []]]);
			wsChatsHasMore = new Map([...wsChatsHasMore, [path, false]]);
		} finally {
			const next = new Set(wsChatsLoading);
			next.delete(path);
			wsChatsLoading = next;
		}
	}

	function reloadWorkspaceChats(path: string) {
		const loadedCount = wsChatsCache.get(path)?.length ?? WS_CHATS_PAGE_SIZE;
		void fetchWorkspaceChats(path, false, Math.max(loadedCount, WS_CHATS_PAGE_SIZE));
	}

	// Projects are expanded by default, so load each workspace's latest chats.
	$effect(() => {
		if (!$chatEnabled) return;
		for (const ws of $workspaceList) {
			if (isExpanded(ws.path) && !wsChatsCache.has(ws.path) && !wsChatsLoading.has(ws.path)) {
				void fetchWorkspaceChats(ws.path);
			}
		}
		if (homeExpanded && !wsChatsCache.has(HOME) && !wsChatsLoading.has(HOME)) {
			void fetchWorkspaceChats(HOME);
		}
	});

	function closeMobileSidebar() {
		if (typeof window !== 'undefined' && window.innerWidth < 768) sidebarOpen.set(false);
	}

	function openWorkspace(e: MouseEvent, path: string) {
		if (e.metaKey || e.ctrlKey) return;
		e.preventDefault();
		goto(`/?workspace=${encodeURIComponent(path)}`);
		closeMobileSidebar();
	}

	function openChat(chatId: string, wsPath: string) {
		if (wsPath === HOME) {
			requestHomeChat(chatId);
			goto('/');
		} else {
			goto(`/?workspace=${encodeURIComponent(wsPath)}&chatId=${encodeURIComponent(chatId)}`);
		}
		closeMobileSidebar();
	}

	function newChat(wsPath: string) {
		if (wsPath === HOME) {
			requestHomeChat();
			goto('/');
		} else {
			goto(`/?workspace=${encodeURIComponent(wsPath)}&chatId`);
		}
		closeMobileSidebar();
	}

	function openWsMenu(e: MouseEvent, path: string) {
		e.stopPropagation();
		e.preventDefault();
		closeChatMenu();
		wsMenuAnchor = e.currentTarget as HTMLElement;
		wsMenuPath = path;
	}

	function closeWsMenu() {
		wsMenuPath = null;
		wsMenuAnchor = null;
	}

	function openChatMenu(e: MouseEvent, chatId: string, wsPath: string) {
		e.stopPropagation();
		closeWsMenu();
		chatMenu = { chatId, wsPath, anchor: e.currentTarget as HTMLElement };
	}

	function closeChatMenu() {
		chatMenu = null;
	}

	async function handleRemoveWorkspace(path: string) {
		closeWsMenu();
		await removeWorkspace(path);
		if (currentPath === path) goto('/');
	}

	async function handleDeleteChat() {
		if (!chatMenu) return;
		const { chatId, wsPath } = chatMenu;
		closeChatMenu();
		await apiDeleteChat(chatId);
		const chats = wsChatsCache.get(wsPath) ?? [];
		wsChatsCache = new Map([...wsChatsCache, [wsPath, chats.filter((chat) => chat.id !== chatId)]]);
		if (currentPath === wsPath && currentChatId === chatId) {
			if (wsPath === HOME) requestHomeChat();
			else goto(`/?workspace=${encodeURIComponent(wsPath)}`);
		}
	}

	async function handleRenameChat() {
		if (!chatMenu) return;
		const { chatId, wsPath } = chatMenu;
		const chat = (wsChatsCache.get(wsPath) ?? []).find((item) => item.id === chatId);
		const title = window.prompt($t('files.rename'), chat?.title)?.trim();
		if (!title || title === chat?.title) return;
		await updateChatTitle(chatId, title);
		const chats = wsChatsCache.get(wsPath) ?? [];
		wsChatsCache = new Map([
			...wsChatsCache,
			[wsPath, chats.map((item) => (item.id === chatId ? { ...item, title } : item))]
		]);
	}

	function copyChatPath() {
		if (!chatMenu) return;
		const { chatId, wsPath } = chatMenu;
		const chat = (wsChatsCache.get(wsPath) ?? []).find((item) => item.id === chatId);
		if (!chat || !wsPath) return;
		navigator.clipboard.writeText(
			`${wsPath.replace(/\/$/, '')}/.cptr/chats/${chat.folder ? `${chat.folder}/` : ''}${chat.id}.json`
		);
	}

	function handleChatEvent(data: {
		type?: string;
		chat_id: string;
		done?: boolean;
		title?: string;
		delta?: string;
		workspace?: string;
		active?: boolean;
		updated_at?: number;
		last_read_at?: number;
		workspace_unread_count?: number;
	}) {
		if (
			!data.title &&
			typeof data.active !== 'boolean' &&
			typeof data.updated_at !== 'number' &&
			typeof data.last_read_at !== 'number' &&
			typeof data.workspace_unread_count !== 'number'
		) {
			return;
		}
		const unreadCount = data.workspace_unread_count;
		if (data.workspace && typeof unreadCount === 'number') {
			workspaceList.update((workspaces) =>
				workspaces.map((workspace) =>
					workspace.path === data.workspace
						? { ...workspace, unread_count: unreadCount }
						: workspace
				)
			);
		}

		let known = false;
		const shouldReorder =
			typeof data.updated_at === 'number' ||
			typeof data.last_read_at === 'number' ||
			typeof data.active === 'boolean';

		wsChatsCache = new Map(
			[...wsChatsCache].map(([path, chats]) => {
				const nextChats = chats.map((chat) => {
					if (chat.id !== data.chat_id) return chat;
					known = true;
					return {
						...chat,
						...(data.title ? { title: data.title } : {}),
						...(typeof data.updated_at === 'number' ? { updated_at: data.updated_at } : {}),
						...(typeof data.last_read_at === 'number' ? { last_read_at: data.last_read_at } : {}),
						...(typeof data.active === 'boolean' ? { is_active: data.active } : {})
					};
				});
				return [path, shouldReorder ? sortChats(nextChats) : nextChats] as [string, ChatInfo[]];
			})
		);

		// A chat created in another session (or the Home slot) is not yet in this
		// sidebar's page. Refresh only that expanded section; known rows update in place.
		const eventPath = typeof data.workspace === 'string' ? data.workspace : null;
		if (eventPath === null || !wsChatsCache.has(eventPath) || !isExpanded(eventPath)) return;
		if (!known) {
			void fetchWorkspaceChats(eventPath);
		} else if (typeof data.last_read_at === 'number') {
			reloadWorkspaceChats(eventPath);
		}
	}

	function isTouchDevice(): boolean {
		return (
			typeof window !== 'undefined' && ('ontouchstart' in window || navigator.maxTouchPoints > 0)
		);
	}

	onMount(() => {
		if (wsListEl && !isTouchDevice()) {
			sortable = Sortable.create(wsListEl, {
				animation: 150,
				ghostClass: 'opacity-30',
				dragClass: 'cursor-grabbing',
				direction: 'vertical',
				handle: '.ws-row',
				onEnd: (evt) => {
					if (evt.oldIndex != null && evt.newIndex != null && evt.oldIndex !== evt.newIndex) {
						reorderWorkspaces(evt.oldIndex, evt.newIndex);
					}
				}
			});
		}

		unbindSocketListener = socketStore.on('events:chat', handleChatEvent);
	});

	onDestroy(() => {
		sortable?.destroy();
		unbindSocketListener?.();
		unbindSocketListener = null;
	});
</script>

{#snippet chatList(path: string, introIndex?: number)}
	{@const chats = wsChatsCache.get(path)}
	{@const hasMoreChats = wsChatsHasMore.get(path)}
	{@const isLoading = wsChatsLoading.has(path)}
	<div
		class="ws-chats"
		data-intro-row={introIndex == null ? undefined : ''}
		style:--intro-i={introIndex}
	>
		{#if isLoading && !chats}
			<div class="ws-chat-loading">
				<span class="ws-chat-loading-dot"></span>
				<span class="ws-chat-loading-dot"></span>
				<span class="ws-chat-loading-dot"></span>
			</div>
		{:else if chats && chats.length > 0}
			{#each chats as chat (chat.id)}
				<ChatItem
					{chat}
					isSelected={chat.id === currentChatId && currentPath === path}
					onclick={() => openChat(chat.id, path)}
					onmenu={(e) => openChatMenu(e, chat.id, path)}
				/>
			{/each}
			{#if hasMoreChats}
				<button
					class="ws-chat-show-more"
					disabled={isLoading}
					onclick={() => fetchWorkspaceChats(path, true)}
				>
					{$t('sidebar.showMore')}
				</button>
			{/if}
		{:else if chats}
			<p class="ws-chat-empty">{$t('sidebar.noChats')}</p>
		{/if}
	</div>
{/snippet}

<div class="flex-1 min-h-0 overflow-y-auto px-1.5 pb-2">
	<!-- Projects -->
	<div class="section-header" data-intro-row style:--intro-i={0}>
		<button
			class="section-toggle"
			onclick={() => toggleCollapsed(PROJECTS_KEY)}
			aria-expanded={projectsExpanded}
			aria-controls="workspace-list"
		>
			<span
				class="section-chevron"
				style="transform: rotate({projectsExpanded ? '90deg' : '0deg'})"
			>
				<Icon name="chevron-right" size={11} />
			</span>
			<span>{$t('sidebar.projects')}</span>
		</button>
		<button
			class="section-action"
			onclick={onaddworkspace}
			aria-label={$t('sidebar.addWorkspace')}
			use:tooltip={$t('sidebar.addWorkspace')}
		>
			<Icon name="plus" size={14} />
		</button>
	</div>

	<div id="workspace-list" bind:this={wsListEl} class:hidden={!projectsExpanded}>
		{#each $workspaceList as ws, index (ws.path)}
			{@const expanded = isExpanded(ws.path)}
			<div class="ws-item" data-intro-row style:--intro-i={index + 1}>
				<div
					class="ws-row group flex items-center gap-1 w-full h-8 px-2 rounded-lg text-[0.8125rem] transition-colors duration-100
					{ws.path === currentPath
						? 'text-gray-900 dark:text-white font-medium'
						: 'text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-200'}"
				>
					<a
						href="/?workspace={encodeURIComponent(ws.path)}"
						class="flex items-center gap-2 flex-1 min-w-0 no-underline text-inherit"
						onclick={(e) => openWorkspace(e, ws.path)}
						title={ws.path}
					>
						{#if $chatEnabled}
							<span
								class="ws-icon-toggle shrink-0"
								role="button"
								tabindex="-1"
								onclick={(e) => {
									e.stopPropagation();
									e.preventDefault();
									toggleCollapsed(ws.path);
								}}
								onkeydown={(e) => {
									if (e.key === 'Enter') toggleCollapsed(ws.path);
								}}
								aria-label={expanded ? $t('sidebar.collapse') : $t('sidebar.expand')}
							>
								<span class="ws-icon-folder"><Icon name="folder" size={15} /></span>
								<span
									class="ws-icon-chevron"
									style="transform: rotate({expanded ? '90deg' : '0deg'})"
								>
									<Icon name="chevron-right" size={11} />
								</span>
							</span>
						{:else}
							<Icon name="folder" size={15} />
						{/if}
						<span class="min-w-0 truncate text-left">{ws.name}</span>
						{#if ws.unread_count > 0}
							<span
								class="inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded-md bg-sky-500/10 px-1 text-[0.625rem] font-semibold text-sky-600 dark:bg-sky-400/10 dark:text-sky-300"
							>
								{new Intl.NumberFormat(undefined, {
									notation: 'compact',
									compactDisplay: 'short'
								}).format(ws.unread_count)}
							</span>
						{/if}
					</a>
					<span
						class="row-action"
						role="button"
						tabindex="-1"
						onclick={(e) => openWsMenu(e, ws.path)}
						onkeydown={() => {}}
						aria-label={$t('sidebar.workspaceOptions')}
					>
						<Icon name="three-dots" size={12} />
					</span>
					{#if $chatEnabled}
						<span
							class="row-action"
							role="button"
							tabindex="-1"
							onclick={() => newChat(ws.path)}
							onkeydown={() => {}}
							aria-label={$t('bar.newChat')}
							use:tooltip={$t('bar.newChat')}
						>
							<Icon name="pencil" size={12} />
						</span>
					{/if}
				</div>

				{#if $chatEnabled && expanded}
					{@render chatList(ws.path)}
				{/if}
			</div>
		{/each}

		{#if $workspaceList.length === 0}
			<button class="ws-chat-empty w-full text-left" onclick={onaddworkspace}>
				{$t('sidebar.noWorkspaces')}
			</button>
		{/if}
	</div>

	<!-- Default workspace: Home chats -->
	{#if $chatEnabled}
		<div class="section-header mt-3" data-intro-row style:--intro-i={$workspaceList.length + 1}>
			<button
				class="section-toggle"
				onclick={() => toggleCollapsed(HOME)}
				aria-expanded={homeExpanded}
			>
				<span class="section-chevron" style="transform: rotate({homeExpanded ? '90deg' : '0deg'})">
					<Icon name="chevron-right" size={11} />
				</span>
				<Icon name="home" size={13} />
				<span>{$t('sidebar.defaultWorkspace')}</span>
			</button>
			<button
				class="section-action"
				onclick={() => newChat(HOME)}
				aria-label={$t('bar.newChat')}
				use:tooltip={$t('bar.newChat')}
			>
				<Icon name="pencil" size={13} />
			</button>
		</div>
		{#if homeExpanded}
			{@render chatList(HOME, $workspaceList.length + 2)}
		{/if}
	{/if}
</div>

{#if wsMenuPath && wsMenuAnchor}
	<DropdownMenu
		anchor={wsMenuAnchor}
		items={[
			{
				label: $t('sidebar.remove'),
				icon: 'xmark',
				onclick: () => handleRemoveWorkspace(wsMenuPath!)
			}
		]}
		onclose={closeWsMenu}
	/>
{/if}

{#if chatMenu}
	<DropdownMenu
		anchor={chatMenu.anchor}
		align="end"
		items={[
			...(chatMenu.wsPath
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
				onclick: handleRenameChat
			},
			{
				label: $t('chat.history.delete'),
				icon: 'trash',
				onclick: handleDeleteChat
			}
		]}
		onclose={closeChatMenu}
	/>
{/if}

<style>
	@reference "../../app.css";

	.section-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		height: 2rem;
		padding: 0 0.25rem 0 0.5rem;
	}

	.section-toggle {
		display: flex;
		flex: 1;
		min-width: 0;
		height: 100%;
		align-items: center;
		gap: 0.375rem;
		font-size: 0.75rem;
		color: var(--app-fg-subtle);
		transition: color 0.1s;
	}

	.section-toggle:hover {
		color: var(--app-fg);
	}

	.section-chevron {
		display: flex;
		transition: transform 0.1s;
	}

	.section-action,
	.row-action {
		display: flex;
		align-items: center;
		justify-content: center;
		flex-shrink: 0;
		border-radius: 0.375rem;
		color: var(--app-fg-subtle);
		transition:
			opacity 0.075s,
			color 0.075s;
	}

	.section-action {
		width: 1.5rem;
		height: 1.5rem;
	}

	.row-action {
		width: 1.25rem;
		height: 1.25rem;
		opacity: 0;
	}

	.ws-row:hover .row-action {
		opacity: 1;
	}

	.section-action:hover,
	.row-action:hover {
		color: var(--app-fg);
	}

	.ws-item {
		margin-bottom: 0.125rem;
	}

	.ws-icon-toggle {
		position: relative;
		display: flex;
		align-items: center;
		justify-content: center;
		width: 0.9375rem;
		height: 0.9375rem;
		cursor: pointer;
	}

	.ws-icon-folder {
		display: flex;
		transition: opacity 0.1s;
	}

	.ws-icon-chevron {
		position: absolute;
		inset: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		opacity: 0;
		transition:
			opacity 0.1s,
			transform 0.1s;
		color: var(--app-fg-subtle);
	}

	.ws-icon-toggle:hover .ws-icon-folder {
		opacity: 0;
	}

	.ws-icon-toggle:hover .ws-icon-chevron {
		opacity: 1;
	}

	.ws-chats {
		margin-top: 0.125rem;
		padding-bottom: 0.25rem;
		padding-left: 1.25rem;
	}

	.ws-chat-show-more,
	.ws-chat-empty {
		display: block;
		padding: 0.25rem 0.5rem;
		border: none;
		background: none;
		font-size: 0.75rem;
		color: var(--app-fg-subtle);
		text-align: left;
	}

	.ws-chat-show-more {
		width: 100%;
		cursor: pointer;
		transition: color 0.1s;
	}

	.ws-chat-show-more:hover {
		color: var(--app-fg);
	}

	.ws-chat-loading {
		display: flex;
		gap: 0.25rem;
		padding: 0.375rem 0.5rem;
	}

	.ws-chat-loading-dot {
		width: 0.25rem;
		height: 0.25rem;
		border-radius: 50%;
		background: var(--app-fg-subtle);
		animation: dotPulse 1s ease-in-out infinite;
	}

	.ws-chat-loading-dot:nth-child(2) {
		animation-delay: 0.15s;
	}

	.ws-chat-loading-dot:nth-child(3) {
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
