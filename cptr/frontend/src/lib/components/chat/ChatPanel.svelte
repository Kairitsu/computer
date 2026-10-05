<script lang="ts">
	import {
		getChat,
		forkChat as apiForkChat,
		sendMessage as apiSendMessage,
		resolveToolCall,
		answerAskUser,
		cancelTask,
		updateCurrentMessage,
		updateMessage,
		createMessage,
		queueSendNow as apiQueueSendNow,
		queueDelete as apiQueueDelete,
		updateChatSettings,
		type ChatMessageRow,
		type ChatSendParams,
		type ReasoningEffort,
		type ToolApprovalMode,
		type ContextUsage,
		type ChatTask
	} from '$lib/apis/chat';
	import {
		chatModels,
		defaultModel,
		setChatReadAt,
		streamingChatTabs,
		registerStreamingChat,
		unregisterStreamingChat,
		updateChatStatuses,
		registerToolApprovalShortcutHandler
	} from '$lib/stores/chat';
	import { socketStore } from '$lib/stores/socket.svelte';
	import { onMount, onDestroy, tick } from 'svelte';
	import { get } from 'svelte/store';
	import {
		currentWorkspace,
		defaultContextWindow,
		defaultReasoningEffort,
		openChatTab,
		streamingBehavior,
		toolApprovalMode as defaultToolApprovalMode,
		widescreenMode
	} from '$lib/stores';
	import { getPathDisplayName } from '$lib/utils/paths';

	import ChatInput from './ChatInput.svelte';
	import UserMessage from './UserMessage.svelte';
	import AssistantMessage from './AssistantMessage.svelte';
	import StatusModal from './StatusModal.svelte';
	import { listCommandSessions, type CommandSession } from '$lib/apis/terminal';
	import Spinner from '../common/Spinner.svelte';
	import Icon from '../Icon.svelte';
	import SuperGrokMark from '../brand/SuperGrokMark.svelte';
	import { quotaData } from '$lib/stores/quota';
	import { tooltip } from '$lib/tooltip';
	import { toast } from 'svelte-sonner';
	import { t } from '$lib/i18n';

	interface Props {
		workspace?: string;
		chatId?: string;
		tabId?: string;
		active?: boolean;
		ontabupdate?: (tabId: string, chatId: string, label: string) => void;
		onopenchat?: (chatId?: string) => void;
		/** New-chat landing only: choose which workspace the chat is created in. */
		onworkspacechange?: (path: string) => void;
	}
	let {
		workspace = '',
		chatId: initialChatId,
		tabId,
		active = true,
		ontabupdate,
		onopenchat,
		onworkspacechange
	}: Props = $props();

	let inputText = $state('');
	let chatId = $state<string | null>(initialChatId ?? null);
	let selectedModel = $state('');
	let toolApprovalMode = $state<ToolApprovalMode>('auto');
	let planMode = $state(false);
	let requestParams = $state<Record<string, unknown>>({});
	let reasoningEffort = $state<ReasoningEffort | null>(null);
	let contextWindow = $state<number | null>(null);
	let allMessages = $state<ChatMessageRow[]>([]);
	const pendingAskUser = $derived.by(() => {
		for (const message of [...allMessages].reverse()) {
			const item = message.output?.find(
				(output: any) =>
					output.type === 'function_call' &&
					output.name === 'ask_user' &&
					output.status === 'pending'
			);
			if (item) {
				return {
					item,
					output: message.output?.find(
						(output: any) =>
							output.type === 'function_call_output' && output.call_id === item.call_id
					),
					chatId,
					messageId: message.id
				};
			}
		}
		return null;
	});
	// Plan reply the user chose to revise; hides its approval card until the next reply.
	let planRevisingId = $state<string | null>(null);
	let currentMessageId = $state<string | null>(null);
	let contextUsage = $state<ContextUsage | null>(null);
	let chatTasks = $state<ChatTask[]>([]);
	let showStatusModal = $state(false);
	let commandSessions = $state<CommandSession[]>([]);
	let initialCommandSessionId = $state<string | null>(null);
	let messagesEl: HTMLDivElement;
	let chatInputEl: ChatInput;
	let statusButtonEl: HTMLButtonElement | undefined = $state();
	let sending = $state(false);
	let autoScroll = $state(true);
	let cancelledMessageId: string | null = null;
	let loading = $state(!!initialChatId);
	let chatTitle = $state('');
	let commandSessionsChatId: string | null = null;
	let taskClearTimer: ReturnType<typeof setTimeout> | null = null;
	let unbindSocketListeners: (() => void) | null = null;
	let commandSessionsTimer: ReturnType<typeof setInterval> | null = null;

	onMount(() => {
		if (initialChatId || typeof sessionStorage === 'undefined') return;
		const key = `cptr:intent:chatDraft:${workspace}`;
		const draft = sessionStorage.getItem(key);
		if (draft) {
			inputText = draft;
			sessionStorage.removeItem(key);
		}
	});

	// ── Windowed rendering ──────────────────────────────────────
	// Only render the last N turns to keep the DOM light for long chats.
	// A "turn" is a user+assistant message pair, so 6 turns = 12 messages.

	const TURNS_PER_PAGE = 6;
	const MESSAGES_PER_PAGE = TURNS_PER_PAGE * 2; // user + assistant
	let visibleCount = $state(MESSAGES_PER_PAGE);

	// Reset visible window when chat changes
	$effect(() => {
		if (chatId) {
			visibleCount = MESSAGES_PER_PAGE;
		}
	});

	// ── Tree-aware path computation ─────────────────────────────

	interface PathEntry {
		msg: ChatMessageRow;
		siblingIds: string[];
		siblingIndex: number;
	}

	function buildChildrenMap(msgs: ChatMessageRow[]): Map<string | null, ChatMessageRow[]> {
		const map = new Map<string | null, ChatMessageRow[]>();
		for (const m of msgs) {
			const key = m.parent_id ?? null;
			if (!map.has(key)) map.set(key, []);
			map.get(key)!.push(m);
		}
		return map;
	}

	function isPendingHiddenMessage(m: ChatMessageRow): boolean {
		return !!m.meta?.queued;
	}

	function nearestVisibleAncestorId(
		messageId: string | null,
		allMsgMap: Map<string, ChatMessageRow>,
		visibleMsgMap: Map<string, ChatMessageRow>
	): string | null {
		let cur = messageId;
		const seen = new Set<string>();
		while (cur && !seen.has(cur)) {
			seen.add(cur);
			if (visibleMsgMap.has(cur)) return cur;
			cur = allMsgMap.get(cur)?.parent_id ?? null;
		}
		return null;
	}

	function findLeaf(messageId: string, childrenMap: Map<string | null, ChatMessageRow[]>): string {
		let cur = messageId;
		while (true) {
			const children = childrenMap.get(cur);
			if (!children?.length) return cur;
			cur = children[children.length - 1].id; // follow latest child
		}
	}

	const activePath = $derived.by((): PathEntry[] => {
		if (!allMessages.length) return [];

		// Exclude pending internal inputs until the parent has processed them.
		const displayMessages = allMessages.filter((m) => !isPendingHiddenMessage(m));
		if (!displayMessages.length) return [];

		const allMsgMap = new Map(allMessages.map((m) => [m.id, m]));
		const msgMap = new Map(displayMessages.map((m) => [m.id, m]));
		const childrenMap = buildChildrenMap(displayMessages);

		// Determine effective currentId. If the persisted current leaf points at
		// a hidden queued/internal input, anchor the display at its visible parent
		// instead of falling onto an unrelated branch.
		const visibleCurrentId = nearestVisibleAncestorId(currentMessageId, allMsgMap, msgMap);
		const effectiveId =
			visibleCurrentId && msgMap.has(visibleCurrentId)
				? visibleCurrentId
				: displayMessages.length > 0
					? displayMessages[displayMessages.length - 1].id
					: null;

		if (!effectiveId) return [];

		// Walk up from effectiveId to root to find ancestor set
		const ancestorIds = new Set<string>();
		let cur: string | null = effectiveId;
		while (cur) {
			ancestorIds.add(cur);
			const msg = msgMap.get(cur);
			cur = msg?.parent_id ?? null;
		}

		// Walk down from root, at each level pick the child in ancestorIds
		const path: PathEntry[] = [];
		let parentId: string | null = null;
		while (true) {
			const siblings = childrenMap.get(parentId);
			if (!siblings?.length) break;
			const next = siblings.find((c) => ancestorIds.has(c.id)) || siblings[0];
			path.push({
				msg: next,
				siblingIds: siblings.map((s) => s.id),
				siblingIndex: siblings.findIndex((s) => s.id === next.id)
			});
			parentId = next.id;
		}

		return path;
	});
	const activePathIds = $derived(new Set(activePath.map(({ msg }) => msg.id)));

	// ── Visible slice of the active path ────────────────────────
	const hasHiddenMessages = $derived(activePath.length > visibleCount);
	const visiblePath = $derived(
		hasHiddenMessages ? activePath.slice(activePath.length - visibleCount) : activePath
	);

	let loadSentinelEl: HTMLDivElement;
	let loadObserver: IntersectionObserver | null = null;
	let loadingMore = false;

	function loadMoreMessages() {
		if (!messagesEl || loadingMore) return;
		loadingMore = true;
		// Remember scroll height before loading more so we can maintain position
		const prevScrollHeight = messagesEl.scrollHeight;

		visibleCount = Math.min(visibleCount + MESSAGES_PER_PAGE, activePath.length);

		// After DOM updates, restore scroll position so the view doesn't jump
		tick().then(() => {
			if (messagesEl) {
				const newScrollHeight = messagesEl.scrollHeight;
				messagesEl.scrollTop += newScrollHeight - prevScrollHeight;
			}
			loadingMore = false;
		});
	}

	// Set up IntersectionObserver to auto-load earlier messages when sentinel is visible
	$effect(() => {
		if (hasHiddenMessages && loadSentinelEl && messagesEl) {
			loadObserver = new IntersectionObserver(
				(entries) => {
					if (entries[0]?.isIntersecting && hasHiddenMessages) {
						loadMoreMessages();
					}
				},
				{ root: messagesEl, threshold: 0 }
			);
			loadObserver.observe(loadSentinelEl);
		}

		return () => {
			if (loadObserver) {
				loadObserver.disconnect();
				loadObserver = null;
			}
		};
	});

	const streaming = $derived(allMessages.some((m) => m.role === 'assistant' && !m.done));
	const isLanding = $derived(allMessages.length === 0 && !chatId);
	/** Landing wordmark follows the Grok CLI plan, like Grok App. */
	const landingPlan = $derived($quotaData?.supergrok?.plan ?? '');
	const landingTitle = $derived($t('chat.landingTitle'));
	// In plan mode, a finished reply is a plan waiting for approval.
	const planApprovalMessageId = $derived.by(() => {
		if (!planMode || streaming || sending || pendingAskUser) return null;
		const last = activePath[activePath.length - 1]?.msg;
		if (!last || last.role !== 'assistant' || !last.done || last.meta?.error) return null;
		return last.content?.trim() ? last.id : null;
	});
	const hasChatContent = $derived(
		activePath.some(({ msg }) => msg.role === 'user' && msg.content.trim())
	);
	const workspaceDisplayName = $derived(
		workspace ? getPathDisplayName(workspace, 'workspace') : 'Computer'
	);
	const displayChatTitle = $derived(chatTitle || firstUserMessageTitle() || workspaceDisplayName);
	const runningCommandSessions = $derived(commandSessions.filter((session) => !session.done));
	const summaryCount = $derived(runningCommandSessions.length);
	const statusButtonClass =
		'text-gray-400 hover:bg-gray-50 hover:text-gray-700 dark:text-gray-600 dark:hover:bg-white/5 dark:hover:text-gray-300';
	const statusTitle = $derived(
		summaryCount > 0 ? `${summaryCount} active command session` : 'Status'
	);

	// Queued messages: user-authored messages waiting behind an active response.
	const queuedMessages = $derived(
		allMessages
			.filter(
				(m) =>
					m.role === 'user' &&
					m.meta?.queued &&
					(m.parent_id ? activePathIds.has(m.parent_id) : activePath.length === 0)
			)
			.map((m) => ({ id: m.id, content: m.content }))
	);

	function setChatTasks(tasks: ChatTask[]) {
		if (taskClearTimer) {
			clearTimeout(taskClearTimer);
			taskClearTimer = null;
		}
		chatTasks = tasks;
		if (
			tasks.length > 0 &&
			!tasks.some((task) => task.status === 'pending' || task.status === 'in_progress')
		) {
			taskClearTimer = setTimeout(() => {
				taskClearTimer = null;
				chatTasks = [];
			}, 4000);
		}
	}

	$effect(() => {
		if (chatId === commandSessionsChatId) return;
		commandSessionsChatId = chatId;
		commandSessions = [];
		if (chatId) refreshCommandSessions();
	});

	// ── Load chat from DB ───────────────────────────────────────

	let loadGeneration = 0;
	let loadedChatId = $state<string | null>(null);

	function markChatRead(id: string) {
		setChatReadAt(id);
		socketStore.getSocket()?.emit('chat:read', { chat_id: id });
	}

	async function loadChat(id: string) {
		chatId = id;
		loadedChatId = null;
		const gen = ++loadGeneration;
		// Only show loading spinner on initial load (no messages yet).
		// On reloads (e.g. after cancel/done), keep the DOM intact to preserve scroll position.
		const isInitialLoad = allMessages.length === 0;
		if (isInitialLoad) loading = true;

		// Snapshot scroll position before replacing messages so we can restore it
		const savedScroll = !isInitialLoad && messagesEl ? messagesEl.scrollTop : -1;

		try {
			const data = await getChat(id);
			// Discard stale response if a newer loadChat was called while we waited
			if (gen !== loadGeneration) return;
			allMessages = data.messages;
			loadChatSettings(data.chat.meta);
			currentMessageId = data.chat.current_message_id;
			contextUsage = data.context_usage ?? null;
			setChatTasks(
				(data.tasks ?? []).some(
					(task) => task.status === 'pending' || task.status === 'in_progress'
				)
					? (data.tasks ?? [])
					: []
			);
			// Update tab label with the real title from the DB
			if (tabId && data.chat.title) {
				chatTitle = data.chat.title;
				updateTab(tabId, id, data.chat.title);
			}
			loadedChatId = id;
			updateChatStatuses([data.chat], workspace);
		} finally {
			if (isInitialLoad && gen === loadGeneration) loading = false;
		}

		// On reloads, restore scroll position after the DOM re-renders.
		// Skip if autoScroll is active — the auto-scroll effect will keep us at the bottom.
		if (savedScroll >= 0 && !autoScroll) {
			await tick();
			if (messagesEl) {
				messagesEl.scrollTop = savedScroll;
				// Also restore after rAF to beat any competing scroll operations
				requestAnimationFrame(() => {
					if (messagesEl) messagesEl.scrollTop = savedScroll;
				});
			}
		}
	}

	// ── Socket listener ─────────────────────────────────────────

	function handleSocketEvent(data: {
		type?: string;
		chat_id: string;
		message_id?: string;
		delta?: string;
		output?: any;
		tasks?: ChatTask[];
		context_usage?: ContextUsage | null;
		done?: boolean;
		error?: string;
		pending_inputs_processed?: boolean;
		title?: string;
		workspace?: string;
		active?: boolean;
		updated_at?: number;
		last_read_at?: number;
	}) {
		if (data.chat_id !== chatId) return;

		if (data.type === 'chat:tasks') {
			setChatTasks(data.tasks ?? []);
			return;
		}

		// Title updates also keep open chat tabs in sync.
		if (data.title && tabId) {
			chatTitle = data.title;
			updateTab(tabId, data.chat_id, data.title);
		}
		if (data.context_usage) {
			contextUsage = data.context_usage;
		}

		// Follow-up state changed server-side: reload to see new transcript/generation state.
		if (data.pending_inputs_processed) {
			loadChat(data.chat_id);
			return;
		}

		if (!data.message_id) return;
		const msg = allMessages.find((m) => m.id === data.message_id);
		if (!msg) return;

		if (data.delta) {
			msg.content += data.delta;
			allMessages = [...allMessages];
		}
		if (data.output) {
			// Merge by call_id to avoid duplicates and update status of existing items
			const existing = msg.output || [];
			const callId = data.output.call_id;
			const itemType = data.output.type;

			if (itemType === 'reasoning') {
				// Responses-style reasoning items stream as output item updates.
				const itemId = data.output.id;
				const existingIdx = itemId
					? existing.findIndex((o: any) => o.type === 'reasoning' && o.id === itemId)
					: existing.findIndex((o: any) => o.type === 'reasoning');
				if (existingIdx >= 0) {
					existing[existingIdx] = data.output;
					msg.output = [...existing];
				} else {
					msg.output = [...existing, data.output];
				}
			} else if (callId && itemType) {
				const existingIdx = existing.findIndex(
					(o: any) => o.type === itemType && o.call_id === callId
				);
				if (existingIdx >= 0) {
					// Update existing item in-place (e.g., pending → completed)
					existing[existingIdx] = { ...existing[existingIdx], ...data.output };
					msg.output = [...existing];
				} else {
					msg.output = [...existing, data.output];
				}
			} else {
				msg.output = [...existing, data.output];
			}
			allMessages = [...allMessages];
		}
		if (data.error) {
			toast.error(data.error, { duration: 8000 });
		}
		if (data.done) {
			// Clear streaming indicator for this tab
			if (tabId) {
				streamingChatTabs.update((s) => {
					const n = new Set(s);
					n.delete(tabId);
					return n;
				});
			}
			// Skip reload for cancelled messages — optimistic state is already correct
			// and reloading causes a visual flash + scroll jump
			if (cancelledMessageId === data.message_id) {
				cancelledMessageId = null;
				return;
			}

			// Mark done optimistically, but keep the streamed content visible until
			// the DB reload returns. This avoids a transient blank message if the
			// final `done` socket event beats the commit/read path.
			msg.done = true;
			if (chatTasks.some((task) => task.status === 'pending' || task.status === 'in_progress')) {
				setChatTasks([]);
			}
			allMessages = [...allMessages];
			loadChat(data.chat_id);
		}
	}

	function handleReconnect() {
		if (chatId) loadChat(chatId);
	}

	function resetChatSettings() {
		const models = get(chatModels);
		const dm = get(defaultModel);
		if (dm) selectedModel = dm;
		else if (models.length) selectedModel = models[0].id;
		toolApprovalMode = get(defaultToolApprovalMode);
		planMode = false;
		requestParams = {};
		reasoningEffort = get(defaultReasoningEffort);
		contextWindow = get(defaultContextWindow);
	}

	function loadChatSettings(meta: Record<string, any> | null) {
		resetChatSettings();
		// Existing chats keep what they were sent with; missing = provider default.
		reasoningEffort = null;
		contextWindow = null;
		const params = meta?.params;
		if (!params || typeof params !== 'object') return;
		if (
			params.tool_approval_mode === 'ask' ||
			params.tool_approval_mode === 'auto' ||
			params.tool_approval_mode === 'full'
		) {
			toolApprovalMode = params.tool_approval_mode;
		}
		planMode = params.plan_mode === true;
		if (params.request_params && typeof params.request_params === 'object') {
			requestParams = params.request_params;
		}
		if (['low', 'medium', 'high', 'xhigh'].includes(params.reasoning_effort)) {
			reasoningEffort = params.reasoning_effort;
		}
		if (typeof params.context_window === 'number' && params.context_window > 0) {
			contextWindow = params.context_window;
		}
		const models = get(chatModels);
		if (
			typeof meta?.last_model === 'string' &&
			models.some((model) => model.id === meta.last_model)
		) {
			selectedModel = meta.last_model;
		}
	}

	async function persistChatSettings() {
		if (!chatId || !selectedModel) return;
		await updateChatSettings(chatId, selectedModel, getChatSendParams()).catch(() => {});
	}

	function handleSettingsChange() {
		// The latest choice becomes the default for the next new chat.
		defaultReasoningEffort.set(reasoningEffort);
		defaultContextWindow.set(contextWindow);
		persistChatSettings();
	}

	onMount(() => {
		resetChatSettings();

		if (chatId) loadChat(chatId);
		refreshCommandSessions();
		commandSessionsTimer = setInterval(refreshCommandSessions, 5000);
		window.addEventListener('computer:inspectCommandSession', handleInspectCommandSession);

		const offChat = socketStore.on('events:chat', handleSocketEvent);
		const offConnect = socketStore.on('connect', handleReconnect);
		unbindSocketListeners = () => {
			offChat();
			offConnect();
		};
	});

	onDestroy(() => {
		unbindSocketListeners?.();
		unbindSocketListeners = null;
		if (commandSessionsTimer) clearInterval(commandSessionsTimer);
		commandSessionsTimer = null;
		window.removeEventListener('computer:inspectCommandSession', handleInspectCommandSession);
		if (taskClearTimer) clearTimeout(taskClearTimer);
		// Don't clear streamingChatTabs here -- the global listener in
		// chat.ts handles cleanup when the "done" event arrives, so the
		// spinner persists even when the chat tab is not active.
	});

	// The model list can arrive after mount (agent CLIs are probed first); pick the
	// default then instead of leaving the composer without a model.
	$effect(() => {
		const models = $chatModels;
		if (!models.length || models.some((model) => model.id === selectedModel)) return;
		const dm = $defaultModel;
		selectedModel = dm && models.some((model) => model.id === dm) ? dm : models[0].id;
	});

	// ── Sync streaming state to shared store for tab icon ────

	$effect(() => {
		if (!tabId) return;
		const tid = tabId;
		streamingChatTabs.update((s) => {
			const next = new Set(s);
			if (streaming) next.add(tid);
			else next.delete(tid);
			return next;
		});
	});

	// ── Register chatId→tabId mapping for global done listener ─

	$effect(() => {
		if (!chatId || !tabId) return;
		registerStreamingChat(chatId, tabId);
		return () => unregisterStreamingChat(chatId!, tabId!);
	});

	$effect(() => {
		if (!active) return;
		return registerToolApprovalShortcutHandler(handleApproveToolCallShortcut);
	});

	// ── Persist read state separately from visibility tracking ──

	$effect(() => {
		if (!chatId || loadedChatId !== chatId || !active) return;
		const id = chatId;
		const send = () => markChatRead(id);
		send();
		return socketStore.on('connect', send);
	});

	$effect(() => {
		if (!chatId || !active) return;
		const id = chatId;
		return () => markChatRead(id);
	});

	$effect(() => {
		if (!chatId || !tabId) return;
		const send = () =>
			socketStore
				.getSocket()
				?.emit('chat:view', { chat_id: chatId, view_id: tabId, visible: active });
		send();
		const off = socketStore.on('connect', send);
		return () => {
			off();
			socketStore
				.getSocket()
				?.emit('chat:view', { chat_id: chatId, view_id: tabId, visible: false });
		};
	});

	// ── Auto-scroll ─────────────────────────────────────────────

	function scrollToBottom() {
		if (!messagesEl) return;
		messagesEl.scrollTop = messagesEl.scrollHeight;
		// Follow-up scroll to account for content-visibility re-layouts
		requestAnimationFrame(() => {
			if (messagesEl) {
				messagesEl.scrollTop = messagesEl.scrollHeight;
			}
		});
	}

	let lastScrollTop = 0;

	function handleMessagesScroll() {
		if (!messagesEl) return;
		const { scrollTop, scrollHeight, clientHeight } = messagesEl;

		// If the user scrolled upward, immediately disengage auto-scroll
		if (scrollTop < lastScrollTop) {
			autoScroll = false;
		} else {
			// Re-engage only when very close to the bottom
			autoScroll = scrollHeight - scrollTop <= clientHeight + 200;
		}

		lastScrollTop = scrollTop;
	}

	$effect(() => {
		if (activePath.length && messagesEl && autoScroll) {
			requestAnimationFrame(() => {
				scrollToBottom();
			});
		}
	});

	// ── Actions ─────────────────────────────────────────────────

	function getChatSendParams(): ChatSendParams {
		const params: ChatSendParams = {
			tool_approval_mode: toolApprovalMode,
			plan_mode: planMode,
			request_params: requestParams
		};
		if (reasoningEffort) params.reasoning_effort = reasoningEffort;
		if (contextWindow) params.context_window = contextWindow;
		return params;
	}

	async function send() {
		let text = inputText.trim();
		if (!text || !selectedModel) return;
		if (sending) return;
		if (hasChatContent && text === '/fork') {
			await handleForkChat();
			return;
		}
		if (text === '/plan') {
			handlePlanCommand();
			inputText = '';
			return;
		}
		if (hasChatContent && text === '/status') {
			handleStatusCommand();
			inputText = '';
			return;
		}
		sending = true;
		const files = chatInputEl?.getFiles() ?? [];
		// Transform TipTap mention format to markdown file links
		text = text.replace(
			/\[@\s+id="([^"]+)"\s+label="([^"]+)"\]/g,
			(_: string, id: string, label: string) => `[${label}](file://${id})`
		);
		inputText = '';
		chatInputEl?.clearUploads();
		autoScroll = true;
		await tick();
		chatInputEl?.resetHeight();

		// parent_id = last message in active path, or null for first message
		const lastMsg = activePath.length > 0 ? activePath[activePath.length - 1].msg : null;
		const parentId = lastMsg?.id ?? null;
		const isNew = !chatId;

		// When streaming and behavior is 'queue', backend will enqueue
		// When streaming and behavior is 'interrupt', cancel first then send normally
		if (streaming && chatId) {
			const behavior = get(streamingBehavior);
			if (behavior === 'interrupt') {
				// Cancel active generation, then fall through to normal send
				const active = allMessages.find((m) => m.role === 'assistant' && !m.done);
				if (active) {
					try {
						await cancelTask(chatId, active.id);
					} catch {}
					await loadChat(chatId);
				}
			} else {
				try {
					const result = await apiSendMessage(
						text,
						selectedModel,
						workspace,
						chatId,
						parentId,
						getChatSendParams(),
						undefined,
						files
					);
					if (result.queued) {
						// Add directly to allMessages so it appears in queue UI instantly
						allMessages = [
							...allMessages,
							{
								id: result.message_id,
								parent_id: parentId,
								role: 'user' as const,
								content: text,
								model: selectedModel,
								done: true,
								output: null,
								usage: null,
								meta: { queued: true },
								created_at: Date.now()
							}
						];
					} else if (result.assistant_message) {
						const newMessages = result.user_message
							? [result.user_message, result.assistant_message]
							: [result.assistant_message];
						allMessages = [...allMessages, ...newMessages];
						currentMessageId = result.message_id;
					}
				} catch (e) {
					console.error('[chat] send (queue) error', e);
				} finally {
					sending = false;
					chatInputEl?.focus();
				}
				return;
			} // end queue behavior
		} // end streaming check

		// ── Normal flow: optimistic UI ────────────────────────────
		const tempId = `temp-${Date.now()}`;
		const optimisticMsg: ChatMessageRow = {
			id: tempId,
			parent_id: parentId,
			role: 'user',
			content: text,
			model: selectedModel,
			done: true,
			output: null,
			usage: null,
			meta: null,
			created_at: Date.now() / 1000
		};
		allMessages = [...allMessages, optimisticMsg];
		currentMessageId = tempId;

		// Update tab label instantly for new chats
		if (isNew && tabId) {
			updateTab(tabId, `pending-${tempId}`, text.slice(0, 40) || $t('chat.fallbackTitle'));
		}

		try {
			const result = await apiSendMessage(
				text,
				selectedModel,
				workspace,
				chatId ?? undefined,
				parentId,
				getChatSendParams(),
				undefined,
				files
			);

			// Swap optimistic temp msg with real messages from backend.
			chatId = result.chat_id;
			const withoutTemp = allMessages.filter((m) => m.id !== tempId);
			if (result.user_message && result.assistant_message) {
				allMessages = [...withoutTemp, result.user_message, result.assistant_message];
			} else if (result.assistant_message) {
				allMessages = [...withoutTemp, result.assistant_message];
			}
			currentMessageId = result.message_id;

			if (isNew && tabId) {
				updateTab(tabId, result.chat_id, text.slice(0, 40) || $t('chat.fallbackTitle'));
			}
		} catch (e) {
			console.error('[chat] send error', e);
			allMessages = allMessages.filter((m) => m.id !== tempId);
			currentMessageId = parentId;
			throw e;
		} finally {
			sending = false;
			chatInputEl?.focus();
		}
	}

	async function handleForkChat(messageId?: string | null) {
		if (!chatId || sending || streaming) return;
		sending = true;
		inputText = '';
		const toastId = toast.loading($t('chat.forking'));
		try {
			const result = await apiForkChat(chatId, messageId ?? currentMessageId);
			if (onopenchat) onopenchat(result.chat_id);
			else openChatTab(result.chat_id);
			toast.success($t('chat.forkDone'), { id: toastId });
		} catch (err: any) {
			toast.error(err?.message || $t('chat.forkFailed'), { id: toastId });
		} finally {
			sending = false;
			chatInputEl?.focus();
		}
	}

	function handlePlanCommand() {
		planMode = !planMode;
		persistChatSettings();
	}

	async function handlePlanApprove() {
		// Leave plan mode and send the approval; anything typed so far rides along.
		const draft = inputText.trim();
		const approval = $t('chat.planApproveMessage');
		planMode = false;
		inputText = draft ? `${approval}\n\n${draft}` : approval;
		await send();
	}

	function handlePlanRevise() {
		planRevisingId = planApprovalMessageId;
		chatInputEl?.focus();
	}

	function handleToolApprovalModeChange(mode: ToolApprovalMode) {
		toolApprovalMode = mode;
		defaultToolApprovalMode.set(mode);
		persistChatSettings();
	}

	function firstUserMessageTitle(): string {
		const message = allMessages.find((m) => m.role === 'user' && !isPendingHiddenMessage(m));
		return message ? message.content.replace(/\s+/g, ' ').trim().slice(0, 80) : '';
	}

	function handleStatusCommand() {
		if (isLanding) return;
		initialCommandSessionId = null;
		showStatusModal = true;
		refreshCommandSessions();
	}

	async function refreshCommandSessions() {
		const currentChatId = chatId;
		if (!currentChatId || !workspace) {
			commandSessions = [];
			return;
		}
		try {
			const sessions = await listCommandSessions(workspace, currentChatId);
			if (chatId !== currentChatId) return;
			commandSessions = sessions;
		} catch (err) {
			console.error('[chat] command sessions refresh error', err);
			if (chatId === currentChatId) commandSessions = [];
		}
	}

	function handleInspectCommandSession(e: Event) {
		const id = (e as CustomEvent<{ commandSessionId?: string }>).detail?.commandSessionId;
		if (!id) return;
		initialCommandSessionId = id;
		showStatusModal = true;
		refreshCommandSessions();
	}

	// ── Queue actions ──────────────────────────────────────────

	async function handleQueueSendNow(messageId: string) {
		if (!chatId) return;
		try {
			await apiQueueSendNow(chatId, messageId);
			await loadChat(chatId);
		} catch (e) {
			console.error('[chat] queue send-now error', e);
		}
	}

	async function handleQueueEdit(messageId: string) {
		if (!chatId) return;
		// Move content back to input, delete from queue
		const msg = allMessages.find((m) => m.id === messageId);
		if (msg) {
			inputText = msg.content;
			chatInputEl?.focus();
		}
		try {
			await apiQueueDelete(chatId, messageId);
			await loadChat(chatId);
		} catch (e) {
			console.error('[chat] queue edit error', e);
		}
	}

	async function handleQueueDelete(messageId: string) {
		if (!chatId) return;
		try {
			await apiQueueDelete(chatId, messageId);
			await loadChat(chatId);
		} catch (e) {
			console.error('[chat] queue delete error', e);
		}
	}

	async function handleCancel() {
		const active = allMessages.find((m) => m.role === 'assistant' && !m.done);
		if (!active || !chatId) return;

		// Tell the socket handler to skip the reload for this message
		cancelledMessageId = active.id;

		// Optimistically mark as done so streaming indicator disappears immediately
		if (active.output) {
			for (const item of active.output) {
				if (item.type === 'function_call' && item.status === 'pending') {
					item.status = 'rejected';
				}
			}
		}

		// Flush any pending streaming text into the output array so it renders
		// when done=true. The AssistantMessage component only shows output items
		// for done messages — raw content is only shown during streaming (!done).
		const flushedText = (active.output || [])
			.filter((i: any) => i.type === 'message')
			.flatMap((i: any) => i.content || [])
			.map((c: any) => c.text)
			.join('');
		const pendingText = (active.content || '').slice(flushedText.length);
		if (pendingText) {
			active.output = [
				...(active.output || []),
				{
					type: 'message',
					id: `flush-${Date.now()}`,
					status: 'completed',
					role: 'assistant',
					content: [{ type: 'output_text', text: pendingText }]
				}
			];
		}

		active.done = true;
		allMessages = [...allMessages];

		try {
			await cancelTask(chatId, active.id);
		} catch (err) {
			console.error('[chat] cancel error', err);
		}
	}

	function handleApprove(messageId: string, callId: string, approved: boolean) {
		if (!chatId) return;

		// Optimistically update local state so Allow/Deny buttons disappear immediately
		const msg = allMessages.find((m) => m.id === messageId);
		if (msg?.output) {
			const call = msg.output.find(
				(item: any) =>
					item.type === 'function_call' && item.call_id === callId && item.status === 'pending'
			);
			if (call) {
				call.status = approved ? 'queued' : 'rejected';
				if (approved) call.approved = true;
				allMessages = [...allMessages]; // trigger reactivity
			}
		}

		resolveToolCall(chatId, messageId, callId, approved ? 'approve' : 'reject').catch((err) => {
			console.error('[chat] approve error', err);
			// Revert on failure: reload from DB to get true state
			loadChat(chatId!);
		});
	}

	function handleApproveToolCallShortcut(approved: boolean) {
		for (const { msg } of [...activePath].reverse()) {
			const item = msg.output?.find(
				(output: any) =>
					output.type === 'function_call' &&
					output.name !== 'ask_user' &&
					output.status === 'pending'
			);
			if (!item) continue;

			handleApprove(msg.id, item.call_id, approved);
			return true;
		}
		return false;
	}

	async function handleAskUserAnswer(
		messageId: string,
		callId: string,
		answers: Record<string, string>,
		timedOut: boolean
	): Promise<void> {
		const id = chatId;
		if (!id) return;
		try {
			await answerAskUser(id, messageId, callId, answers, timedOut);
		} catch (err) {
			console.error('[chat] ask_user answer error', err);
			if (err instanceof Error && err.message) toast.error(err.message);
			loadChat(id);
			// Rethrow so the question card re-enables its submit button.
			throw err;
		}
	}

	function handleNavigate(messageId: string, direction: -1 | 1) {
		const entry = activePath.find((p) => p.msg.id === messageId);
		if (!entry) return;

		const newIndex = entry.siblingIndex + direction;
		if (newIndex < 0 || newIndex >= entry.siblingIds.length) return;

		const targetId = entry.siblingIds[newIndex];
		const childrenMap = buildChildrenMap(allMessages);
		const leafId = findLeaf(targetId, childrenMap);

		currentMessageId = leafId;
		if (chatId) updateCurrentMessage(chatId, leafId).catch(() => {});
	}

	async function handleRegenerate(messageId: string) {
		const msg = allMessages.find((m) => m.id === messageId);
		if (!msg?.parent_id || !chatId || !selectedModel) return;

		try {
			const result = await apiSendMessage(
				'',
				selectedModel,
				workspace,
				chatId,
				msg.parent_id,
				getChatSendParams()
			);
			if (result.assistant_message) {
				allMessages = [...allMessages, result.assistant_message];
				currentMessageId = result.message_id;
			}
		} catch (e) {
			console.error('[chat] regenerate error', e);
		}
	}

	async function handleEditMessage(
		messageId: string,
		content: string,
		output: any[] | null,
		submit: boolean
	) {
		const msg = allMessages.find((m) => m.id === messageId);
		if (!msg || !chatId) return;

		if (!submit) {
			// Save in-place
			const updates: { content?: string; output?: any[] } = {};
			if (msg.role === 'user') {
				updates.content = content;
			} else {
				updates.content = content;
				if (output) updates.output = output;
			}
			await updateMessage(chatId, messageId, updates);
			await loadChat(chatId);
			return;
		}

		if (msg.role === 'user') {
			// Send: create new sibling user message + trigger LLM
			try {
				const result = await apiSendMessage(
					content,
					selectedModel,
					workspace,
					chatId,
					msg.parent_id,
					getChatSendParams()
				);
				if (result.user_message && result.assistant_message) {
					allMessages = [...allMessages, result.user_message, result.assistant_message];
					currentMessageId = result.message_id;
				}
			} catch (e) {
				console.error('[chat] edit-send error', e);
			}
		} else {
			// Save As Copy: create new sibling assistant message (no LLM)
			try {
				await createMessage(
					chatId,
					msg.parent_id ?? null,
					'assistant',
					content,
					output ?? undefined
				);
				await loadChat(chatId);
			} catch (e) {
				console.error('[chat] save-as-copy error', e);
			}
		}
	}

	function updateTab(tid: string, newChatId: string, label: string) {
		if (ontabupdate) return ontabupdate(tid, newChatId, label);
		currentWorkspace.update((ws) => {
			if (!ws) return ws;
			return {
				...ws,
				groups: ws.groups.map((g) => ({
					...g,
					tabs: g.tabs.map((t) => (t.id === tid ? { ...t, path: newChatId, label } : t))
				}))
			};
		});
	}

	function handleNewChatCommand() {
		if (onopenchat) return onopenchat();
		openChatTab();
	}
</script>

<div
	class="app-theme relative flex h-full flex-col"
	style="background: var(--app-bg); color: var(--app-fg);"
>
	{#if !isLanding}
		<div
			class="relative z-30 -mb-12 flex h-7 shrink-0 items-center gap-2 pl-3 pr-2 dark:border-white/6"
			style="border-color: color-mix(in oklab, var(--app-fg) 8%, transparent);"
		>
			<div
				aria-hidden="true"
				class="pointer-events-none absolute inset-0 -bottom-10 -z-10"
				style="background: linear-gradient(to bottom, var(--app-bg), color-mix(in oklab, var(--app-bg) 95%, transparent) 40%, transparent 97%);"
			></div>
			<div
				class="min-w-0 flex-1 truncate text-[0.6875rem] font-medium text-gray-600 dark:text-gray-400"
			>
				{displayChatTitle}
			</div>
			<div class="flex shrink-0 items-center gap-0.5">
				<span
					class="mr-1 flex items-center gap-1 text-[0.6875rem] text-gray-400 dark:text-gray-500"
					aria-live="polite"
				>
					<span class="status-dot" class:running={streaming || sending}></span>
					{streaming || sending ? $t('chat.statusRunning') : $t('chat.statusIdle')}
				</span>
				<button
					type="button"
					class="relative flex size-6 shrink-0 items-center justify-center rounded-lg transition-colors duration-75 {statusButtonClass}"
					aria-label={$t('bar.newChat')}
					title={$t('bar.newChat')}
					use:tooltip={$t('bar.newChat')}
					onclick={handleNewChatCommand}
				>
					<Icon name="chat-plus" size={14} />
				</button>
				<button
					bind:this={statusButtonEl}
					type="button"
					class="relative flex size-6 shrink-0 items-center justify-center rounded-lg transition-colors duration-75 {statusButtonClass}"
					aria-label={statusTitle}
					title={statusTitle}
					use:tooltip={statusTitle}
					onclick={handleStatusCommand}
				>
					<Icon name="list" size={13} />
				</button>
			</div>
		</div>
	{/if}

	{#if isLanding}
		<!-- Landing: Grok-style new chat -->
		<div class="flex-1 overflow-y-auto flex flex-col">
			<div class="max-w-3xl w-full mx-auto px-4 flex flex-col my-auto pt-6 pb-16">
				<div class="landing-mark mb-10" data-intro-landing>
					<div class="landing-brand text-gray-900 dark:text-white">
						<SuperGrokMark heavy={/heavy/i.test(landingPlan)} title={landingPlan || undefined} />
					</div>
					<h1
						class="landing-prompt text-gray-900 dark:text-white"
						style:--prompt-steps={Math.max(1, Array.from(landingTitle).length)}
					>
						{landingTitle}
					</h1>
				</div>

				<ChatInput
					bind:this={chatInputEl}
					bind:inputText
					bind:selectedModel
					bind:toolApprovalMode
					bind:planMode
					bind:requestParams
					bind:reasoningEffort
					bind:contextWindow
					{sending}
					{workspace}
					placeholder={$t('chat.landingPlaceholder')}
					tasks={chatTasks}
					askUser={pendingAskUser}
					onaskuseranswer={handleAskUserAnswer}
					onsend={send}
					onplan={handlePlanCommand}
					onsettingschange={handleSettingsChange}
					ontoolapprovalchange={handleToolApprovalModeChange}
					{onworkspacechange}
					{queuedMessages}
					onqueuesendnow={handleQueueSendNow}
					onqueueedit={handleQueueEdit}
					onqueuedelete={handleQueueDelete}
				/>
			</div>
		</div>
	{:else}
		<!-- Conversation view -->
		{#if loading}
			<div class="flex-1 flex items-center justify-center text-gray-400 dark:text-gray-500">
				<Spinner size={24} />
			</div>
		{:else}
			<div
				bind:this={messagesEl}
				class="flex-1 overflow-y-auto"
				style="background: var(--app-bg); color: var(--app-fg);"
				onscroll={handleMessagesScroll}
			>
				<div
					class="{$widescreenMode
						? 'max-w-full'
						: 'max-w-2xl'} mx-auto w-full px-4 pt-16 pb-16 flex flex-col gap-4"
				>
					{#if hasHiddenMessages}
						<div bind:this={loadSentinelEl} class="h-1 w-full" aria-hidden="true"></div>
					{/if}
					{#each visiblePath as { msg, siblingIds, siblingIndex } (msg.id)}
						{#if msg.role === 'user'}
							<UserMessage
								content={msg.content}
								meta={msg.meta}
								createdAt={msg.created_at}
								{siblingIndex}
								siblingTotal={siblingIds.length}
								onnavigate={(dir) => handleNavigate(msg.id, dir)}
								onedit={(c, submit) => handleEditMessage(msg.id, c, null, submit)}
							/>
						{:else}
							<AssistantMessage
								content={msg.content}
								meta={msg.meta}
								done={msg.done}
								output={msg.output}
								usage={msg.usage}
								{chatId}
								messageId={msg.id}
								createdAt={msg.created_at}
								{siblingIndex}
								siblingTotal={siblingIds.length}
								onnavigate={(dir) => handleNavigate(msg.id, dir)}
								onfork={sending || streaming ? undefined : () => handleForkChat(msg.id)}
								onregenerate={() => handleRegenerate(msg.id)}
								onedit={(c, o, submit) => handleEditMessage(msg.id, c, o, submit)}
								onapprove={handleApprove}
							/>
						{/if}
					{/each}
				</div>
			</div>
		{/if}

		<!-- Input area -->
		<div class="px-4 py-3" style="background: var(--app-bg);">
			<div class="{$widescreenMode ? 'max-w-full' : 'max-w-2xl'} mx-auto w-full relative">
				{#if !autoScroll && activePath.length > 0}
					<div
						class="absolute -top-10 left-0 right-0 pr-2 flex justify-end z-30 pointer-events-none"
					>
						<button
							class="app-surface app-interactive border p-1 rounded-full pointer-events-auto shadow-sm transition-colors"
							onclick={() => {
								autoScroll = true;
								scrollToBottom();
							}}
							aria-label={$t('chat.scrollToBottom')}
						>
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 20 20"
								fill="currentColor"
								class="w-4 h-4 text-gray-500 dark:text-gray-400"
							>
								<path
									fill-rule="evenodd"
									d="M10 3a.75.75 0 01.75.75v10.638l3.96-4.158a.75.75 0 111.08 1.04l-5.25 5.5a.75.75 0 01-1.08 0l-5.25-5.5a.75.75 0 111.08-1.04l3.96 4.158V3.75A.75.75 0 0110 3z"
									clip-rule="evenodd"
								/>
							</svg>
						</button>
					</div>
				{/if}
				<ChatInput
					bind:this={chatInputEl}
					bind:inputText
					bind:selectedModel
					bind:toolApprovalMode
					bind:planMode
					bind:requestParams
					bind:reasoningEffort
					bind:contextWindow
					{sending}
					{streaming}
					{workspace}
					placeholder={$t('chat.landingPlaceholder')}
					{contextUsage}
					{hasChatContent}
					tasks={chatTasks}
					askUser={pendingAskUser}
					onaskuseranswer={handleAskUserAnswer}
					planApproval={!!planApprovalMessageId && planApprovalMessageId !== planRevisingId}
					onplanapprove={handlePlanApprove}
					onplanrevise={handlePlanRevise}
					onsend={send}
					onfork={handleForkChat}
					onplan={handlePlanCommand}
					onsettingschange={handleSettingsChange}
					ontoolapprovalchange={handleToolApprovalModeChange}
					onstatus={handleStatusCommand}
					oncancel={handleCancel}
					{queuedMessages}
					onqueuesendnow={handleQueueSendNow}
					onqueueedit={handleQueueEdit}
					onqueuedelete={handleQueueDelete}
				/>
			</div>
		</div>
	{/if}
</div>

{#if showStatusModal}
	<StatusModal
		{chatId}
		{contextUsage}
		{commandSessions}
		{queuedMessages}
		{initialCommandSessionId}
		anchor={statusButtonEl ?? { x: 0, y: 0 }}
		onclose={() => {
			showStatusModal = false;
			initialCommandSessionId = null;
		}}
	/>
{/if}

<style>
	/* Landing, after Grok App: the wordmark rises out of a mask, then the
	 * prompt types in. Plays whenever a new chat opens; on first load it waits
	 * for the sidebar (html.cptr-intro, see app.css). */
	.landing-mark {
		--brand-delay: 60ms;
		--prompt-delay: 520ms;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.875rem;
		text-align: center;
		user-select: none;
	}

	:global(html.cptr-intro) .landing-mark {
		--brand-delay: 480ms;
		--prompt-delay: 940ms;
	}

	.landing-brand {
		display: flex;
		justify-content: center;
		max-width: 100%;
		overflow: hidden;
		padding: 0.25rem 0.5rem;
	}

	.landing-brand :global(svg) {
		animation: landing-brand-rise 560ms cubic-bezier(0.22, 1, 0.36, 1) var(--brand-delay) backwards;
	}

	.landing-prompt {
		width: max-content;
		max-width: calc(100% - 2rem);
		font-size: clamp(1.35rem, 2.4vw, 1.75rem);
		font-weight: 600;
		line-height: 1.2;
		letter-spacing: -0.02em;
		text-wrap: balance;
		animation: landing-prompt-type 680ms steps(var(--prompt-steps), end) var(--prompt-delay)
			backwards;
	}

	@keyframes landing-brand-rise {
		from {
			opacity: 0;
			transform: translate3d(0, calc(100% + 0.75rem), 0);
		}
	}

	@keyframes landing-prompt-type {
		from {
			clip-path: inset(0 100% 0 0);
		}
		to {
			clip-path: inset(0);
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.landing-brand :global(svg),
		.landing-prompt {
			animation: none;
		}
	}

	.status-dot {
		width: 0.375rem;
		height: 0.375rem;
		border-radius: 999px;
		background: color-mix(in oklab, var(--app-fg) 35%, transparent);
	}

	.status-dot.running {
		background: #10b981;
		animation: statusPulse 1.2s ease-in-out infinite;
	}

	@keyframes statusPulse {
		50% {
			opacity: 0.4;
		}
	}
</style>
