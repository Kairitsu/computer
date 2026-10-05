<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import {
		currentWorkspace,
		workspaceList,
		addWorkspace,
		loadWorkspace,
		gitReviewOpen,
		setActiveGroup,
		setSplitRatio,
		moveTabToNewSplit,
		openChatTab,
		openFileTab,
		setFileBrowserCwd,
		showSearch,
		pwaPreferences,
		homeState,
		splitHomeTab,
		closeHomeGroup,
		setHomeActiveGroup,
		moveHomeTabToNewSplit,
		moveHomeTabToGroup,
		setHomeSplitRatio,
		homeChatRequest,
		releaseClosedChatTab
	} from '$lib/stores';
	import type { Tab, EditorGroup, EditorLayout, SplitDirection, WorkspaceState } from '$lib/stores';
	import { chatEnabled } from '$lib/stores/chat';
	import { get } from 'svelte/store';
	import { untrack } from 'svelte';
	import { createEntry, writeFile, uploadFiles as uploadFilesApi } from '$lib/apis/files';
	import { getChat } from '$lib/apis/chat';
	import { deleteSharePayload, getSharePayload } from '$lib/intents/payloadStore';
	import type { LaunchIntent, ShareBehavior, SharePayload } from '$lib/intents/types';
	import FileBrowser from '$lib/components/FileBrowser.svelte';
	import FileEditor from '$lib/components/FileEditor.svelte';
	import GitView from '$lib/components/GitView.svelte';
	import ChatPanel from '$lib/components/chat/ChatPanel.svelte';
	import DirectoryPicker from '$lib/components/DirectoryPicker.svelte';
	import GroupTabBar from '$lib/components/GroupTabBar.svelte';
	import WorkspacePicker from '$lib/components/WorkspacePicker.svelte';
	import { TAB_DRAG_MIME } from '$lib/constants';
	import { isSupportedWorkspacePath } from '$lib/utils/paths';

	let showPicker = $state(false);
	let pendingIntent = $state<LaunchIntent | null>(null);
	let folderPickerIntent = $state<LaunchIntent | null>(null);
	let folderPickerWorkspace = $state<string | null>(null);
	// The permanent Home tab is a chat slot: a new chat until sent, then that chat.
	// Bumping the key remounts it for a different chat or a fresh one.
	let homeChatKey = $state(0);
	let homeChatWorkspace = $state('');
	const activeHomeGroup = $derived(
		$homeState.groups.find((group) => group.id === $homeState.activeGroupId) ?? $homeState.groups[0]
	);
	function updateHomeTabs(
		groupId: string,
		update: (tabs: Tab[]) => { tabs: Tab[]; activeTabId: string }
	) {
		homeState.update((state) => ({
			...state,
			activeGroupId: groupId,
			groups: state.groups.map((group) =>
				group.id === groupId ? { ...group, ...update(group.tabs) } : group
			)
		}));
	}

	function reorderHomeTabs(groupId: string, oldIndex: number, newIndex: number) {
		const group = $homeState.groups.find((item) => item.id === groupId);
		if (!group) return;
		const tabs = [...group.tabs];
		const [tab] = tabs.splice(oldIndex, 1);
		if (!tab) return;
		tabs.splice(newIndex, 0, tab);
		updateHomeTabs(groupId, () => ({ tabs, activeTabId: group.activeTabId }));
	}

	function cycleHomeTab(direction: 1 | -1) {
		const group = activeHomeGroup;
		if (!group || group.tabs.length < 2) return;
		const currentIndex = group.tabs.findIndex((tab) => tab.id === group.activeTabId);
		if (currentIndex === -1) return;
		const nextIndex = (currentIndex + direction + group.tabs.length) % group.tabs.length;
		updateHomeTabs(group.id, (tabs) => ({ tabs, activeTabId: tabs[nextIndex].id }));
	}

	function toggleHomeSplit() {
		if ($homeState.groups.length > 1) {
			const group = $homeState.groups.find((item) => item.id !== $homeState.activeGroupId);
			if (group) closeHomeGroup(group.id);
		} else {
			splitHomeTab();
		}
	}

	function homeSlotGroupId(): string | undefined {
		return (
			$homeState.groups.find((group) => group.tabs.some((tab) => tab.type === 'home'))?.id ??
			$homeState.groups[0]?.id
		);
	}

	/** Load a chat (or a fresh new chat) into the Home tab's chat slot. */
	function loadHomeSlot(chatId?: string) {
		const groupId = homeSlotGroupId();
		const group = $homeState.groups.find((item) => item.id === groupId);
		const homeTab = group?.tabs.find((tab) => tab.type === 'home');
		if (!groupId || !homeTab) return;
		const unchanged = homeTab.path === chatId;
		updateHomeTabs(groupId, (tabs) => ({
			tabs: tabs.map((tab) =>
				tab.id === homeTab.id
					? { ...tab, path: chatId, label: unchanged ? tab.label : chatId ? 'Chat' : 'New Chat' }
					: tab
			),
			activeTabId: homeTab.id
		}));
		if (unchanged) return;
		homeChatWorkspace = '';
		homeChatKey += 1;
	}

	function openHomeChat(chatId?: string) {
		if (!chatId) return loadHomeSlot();
		for (const group of $homeState.groups) {
			const existing = group.tabs.find(
				(tab) => (tab.type === 'chat' || tab.type === 'home') && tab.path === chatId
			);
			if (existing) {
				updateHomeTabs(group.id, (tabs) => ({ tabs, activeTabId: existing.id }));
				return;
			}
		}
		loadHomeSlot(chatId);
	}

	/** Tab bar "+ → New chat": a separate chat tab next to the Home slot. */
	function openHomeChatTab(groupId = $homeState.activeGroupId) {
		const tab: Tab = {
			id: `home-${Date.now()}`,
			type: 'chat',
			label: 'New Chat',
			path: `new-${Date.now()}`
		};
		updateHomeTabs(groupId, (tabs) => ({ tabs: [...tabs, tab], activeTabId: tab.id }));
	}

	function closeHomeTab(tabId: string, groupId = $homeState.activeGroupId) {
		const group = $homeState.groups.find((item) => item.id === groupId);
		if (!group) return;
		const index = group.tabs.findIndex((tab) => tab.id === tabId);
		const tab = group.tabs[index];
		if (!tab || tab.permanent) return;
		releaseClosedChatTab(tab);
		const tabs = group.tabs.filter((item) => item.id !== tabId);
		const activeTabId =
			group.activeTabId === tabId
				? (tabs[Math.max(0, index - 1)]?.id ?? 'home')
				: group.activeTabId;
		updateHomeTabs(groupId, () => ({ tabs, activeTabId }));
		if (!tabs.length) closeHomeGroup(groupId);
	}

	function updateHomeChatTab(
		tabId: string,
		chatId: string,
		label: string,
		groupId = $homeState.activeGroupId
	) {
		const group = $homeState.groups.find((item) => item.id === groupId);
		if (!group) return;
		updateHomeTabs(groupId, (tabs) => ({
			tabs: tabs.map((tab) => (tab.id === tabId ? { ...tab, path: chatId, label } : tab)),
			activeTabId: group.activeTabId
		}));
	}

	const INTENT_URL_KEYS = [
		'intent',
		'chatId',
		'file',
		'dir',
		'targetDir',
		'payload',
		'shareBehavior',
		'title',
		'text',
		'url'
	];

	// ── URL-driven workspace loading ───────────────────────────────
	// The workspace path comes from the URL query param: ?workspace=/path/to/dir
	// Each browser tab has its own URL → its own workspace.
	// Intent params (chatId, file, dir) are processed AFTER loading.

	let lastLoadedPath = $state<string | null>(null);
	type LaunchQueueWindow = Window & {
		__cptrLaunchQueueBound?: boolean;
		launchQueue?: {
			setConsumer: (
				consumer: (params: { files?: { getFile: () => Promise<File> }[] }) => void
			) => void;
		};
	};

	function urlIntent(url: URL): LaunchIntent | null {
		const protocol = webCptrIntent(url.searchParams.get('intent'));
		const params = protocol?.params ?? url.searchParams;
		const intent = protocol?.intent ?? url.searchParams.get('intent');
		const workspace = params.get('workspace') || undefined;
		const chatId = params.get('chatId');
		const filePath = params.get('file');
		const dirPath = params.get('dir');

		if (intent === 'newNote') return { kind: 'newNote', workspace };
		if (intent === 'newChat') return { kind: 'newChat', workspace };
		if (intent === 'openWorkspace') return { kind: 'openWorkspace' };
		if (intent === 'search') return { kind: 'search', workspace };
		if (intent === 'importFiles') {
			return { kind: 'importFiles', workspace, targetDir: params.get('targetDir') || undefined };
		}
		if (intent === 'share') {
			return {
				kind: 'share',
				workspace,
				payloadId: params.get('payload') || undefined,
				targetDir: params.get('targetDir') || undefined,
				shareBehavior: (params.get('shareBehavior') || undefined) as ShareBehavior | undefined,
				share: {
					title: params.get('title') || undefined,
					text: params.get('text') || undefined,
					url: params.get('url') || undefined
				}
			};
		}
		if (chatId !== null) return { kind: 'openChat', workspace, chatId: chatId || null };
		if (filePath) return { kind: 'openFile', workspace, filePath };
		if (dirPath) return { kind: 'openDir', workspace, dirPath };
		return null;
	}

	function webCptrIntent(
		raw: string | null
	): { intent: string | null; params: URLSearchParams } | null {
		if (!raw) return null;
		let decoded = raw;
		try {
			decoded = decodeURIComponent(raw);
		} catch {}
		if (!decoded.startsWith('web+cptr:')) return null;
		try {
			const parsed = new URL(decoded);
			return {
				intent:
					parsed.searchParams.get('intent') ||
					parsed.hostname ||
					parsed.pathname.replace(/^\/+/, '') ||
					null,
				params: parsed.searchParams
			};
		} catch {
			const rest = decoded.replace(/^web\+cptr:(\/\/)?/, '');
			const [intentPart, query = ''] = rest.split('?');
			return { intent: intentPart.replace(/^\/+/, '') || null, params: new URLSearchParams(query) };
		}
	}

	function clearIntentUrl(url: URL): string {
		const next = new URL(url);
		for (const key of INTENT_URL_KEYS) next.searchParams.delete(key);
		return next.pathname + next.search;
	}

	function noteBaseName(now = new Date()): string {
		const pad = (n: number) => String(n).padStart(2, '0');
		return `note-${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}-${pad(
			now.getHours()
		)}${pad(now.getMinutes())}`;
	}

	function shareMarkdown(payload: SharePayload): string {
		const lines: string[] = [];
		if (payload.title) lines.push(`# ${payload.title.trim()}`, '');
		if (payload.text) lines.push(payload.text.trim(), '');
		if (payload.url) lines.push(payload.url.trim(), '');
		if (payload.files?.length) {
			lines.push('Files:', ...payload.files.map((file) => `- ${file.name}`), '');
		}
		return lines.join('\n').trimEnd() + '\n';
	}

	async function processIntentParams() {
		const url = new URL($page.url);
		const parsed = urlIntent(url);
		if (parsed) history.replaceState(history.state, '', clearIntentUrl(url));
		if (!parsed) return;
		await handleIntent(parsed);
	}

	async function handleIntent(intent: LaunchIntent) {
		if (intent.kind === 'openWorkspace') {
			showPicker = true;
			return;
		}

		const targetWorkspace = intent.workspace;
		const needsWorkspace = intentNeedsExplicitWorkspace(intent.kind);
		if (needsWorkspace && !targetWorkspace) {
			pendingIntent = intent;
			return;
		}

		switch (intent.kind) {
			case 'newNote':
				await createNote(targetWorkspace!);
				break;
			case 'newChat':
				if (targetWorkspace) openChatTab();
				else openHomeChat();
				break;
			case 'search':
				showSearch.set(true);
				break;
			case 'openChat':
				if (targetWorkspace) {
					openChatTab(intent.chatId ?? undefined);
				} else if (intent.chatId) {
					const chat = await getChat(intent.chatId).catch(() => null);
					const chatWorkspace = chat?.chat.meta?.workspace;
					if (typeof chatWorkspace === 'string' && chatWorkspace) {
						addWorkspace(chatWorkspace);
						await goto(
							`/?workspace=${encodeURIComponent(chatWorkspace)}&chatId=${encodeURIComponent(intent.chatId)}`
						);
					} else {
						openHomeChat(intent.chatId);
					}
				} else {
					openHomeChat();
				}
				break;
			case 'openFile':
				if (intent.filePath) openFileTab(intent.filePath);
				break;
			case 'openDir':
				if (intent.dirPath) setFileBrowserCwd(intent.dirPath);
				break;
			case 'share':
				await handleShareIntent(intent, targetWorkspace!);
				break;
			case 'importFiles':
				await importIntentFiles(intent, targetWorkspace!);
				break;
		}
	}

	function intentNeedsExplicitWorkspace(kind: LaunchIntent['kind']): boolean {
		return ['newNote', 'openFile', 'openDir', 'share', 'importFiles'].includes(kind);
	}

	async function createNote(workspacePath: string, content = '') {
		if (get(currentWorkspace)?.path !== workspacePath) {
			await loadWorkspace(workspacePath);
		}

		const base = noteBaseName();
		for (let i = 1; i < 100; i += 1) {
			const suffix = i === 1 ? '' : `-${i}`;
			const path = `${workspacePath}/${base}${suffix}.md`;
			try {
				await createEntry(path, 'file');
				if (content) await writeFile(path, content);
				openFileTab(path, undefined, { edit: true });
				return path;
			} catch (e: any) {
				if (e?.status !== 409) throw e;
			}
		}
		throw new Error('Could not create note');
	}

	async function handleShareIntent(intent: LaunchIntent, workspacePath: string) {
		const behavior = intent.shareBehavior ?? get(pwaPreferences).shareBehavior;
		if (behavior === 'ask') {
			pendingIntent = intent;
			return;
		}

		let share = intent.share;
		if (intent.payloadId) {
			share = (await getSharePayload(intent.payloadId)) ?? share;
			await deleteSharePayload(intent.payloadId).catch(() => {});
		}
		if (!share) return;

		if (behavior === 'chatDraft') {
			sessionStorage.setItem(`cptr:intent:chatDraft:${workspacePath}`, shareMarkdown(share));
			openChatTab();
			return;
		}
		await createNote(workspacePath, shareMarkdown(share));
	}

	async function importIntentFiles(intent: LaunchIntent, workspacePath: string) {
		const files = intent.importFiles?.files;
		if (!files?.length) return;
		const prefs = get(pwaPreferences);
		if (prefs.importDestination === 'askFolder' && !intent.targetDir) {
			folderPickerIntent = intent;
			folderPickerWorkspace = workspacePath;
			showPicker = true;
			return;
		}
		const directory = resolveImportDirectory(workspacePath, intent.targetDir);
		for (const file of files) {
			const form = new FormData();
			form.append('file', file, file.name);
			form.append('directory', directory);
			await uploadFilesApi(directory, form);
		}
	}

	function resolveImportDirectory(workspacePath: string, targetDir?: string): string {
		if (targetDir) return targetDir;
		const prefs = get(pwaPreferences);
		if (prefs.importDestination !== 'configuredFolder' || !prefs.importFolder?.trim()) {
			return workspacePath;
		}
		const folder = prefs.importFolder.trim();
		if (folder.startsWith('/')) return folder;
		return `${workspacePath}/${folder.replace(/^\/+/, '')}`;
	}

	async function chooseIntentWorkspace(path: string, behavior?: ShareBehavior) {
		if (!pendingIntent) return;
		addWorkspace(path);
		const intent: LaunchIntent = {
			...pendingIntent,
			workspace: path,
			shareBehavior: behavior ?? pendingIntent.shareBehavior
		};
		pendingIntent = null;
		const params = new URLSearchParams({ workspace: path });
		await goto(`/?${params.toString()}`, { replaceState: true });
		if (get(currentWorkspace)?.path !== path) await loadWorkspace(path);
		await handleIntent(intent);
	}

	function handlePickedImportFolder(path: string) {
		if (!folderPickerIntent || !folderPickerWorkspace) return;
		const intent: LaunchIntent = {
			...folderPickerIntent,
			workspace: folderPickerWorkspace,
			targetDir: path
		};
		folderPickerIntent = null;
		folderPickerWorkspace = null;
		showPicker = false;
		void handleIntent(intent);
	}

	$effect(() => {
		const workspacePath = $page.url.searchParams.get('workspace');
		if (workspacePath && !isSupportedWorkspacePath(workspacePath)) {
			lastLoadedPath = null;
			currentWorkspace.set(null);
			goto('/', { replaceState: true });
			return;
		}
		if (workspacePath && workspacePath !== lastLoadedPath) {
			// New workspace — load then process intents
			lastLoadedPath = workspacePath;
			loadWorkspace(workspacePath).then(async () => {
				const canonicalWorkspacePath = get(currentWorkspace)?.path;
				if (canonicalWorkspacePath && canonicalWorkspacePath !== workspacePath) {
					const params = new URLSearchParams($page.url.searchParams);
					params.set('workspace', canonicalWorkspacePath);
					lastLoadedPath = canonicalWorkspacePath;
					await goto(`/?${params.toString()}`, { replaceState: true });
				}
				processIntentParams();
			});
		} else if (workspacePath && workspacePath === lastLoadedPath) {
			// Same workspace — process intents immediately
			if (get(currentWorkspace)?.path === workspacePath) processIntentParams();
		} else if (!workspacePath) {
			lastLoadedPath = null;
			currentWorkspace.set(null);
			processIntentParams();
		}
	});

	$effect(() => {
		if (typeof window === 'undefined') return;
		const w = window as LaunchQueueWindow;
		if (!w.launchQueue || w.__cptrLaunchQueueBound) return;
		w.__cptrLaunchQueueBound = true;
		w.launchQueue.setConsumer(async (launchParams) => {
			const handles = launchParams.files ?? [];
			if (!handles.length) return;
			const files: File[] = [];
			for (const handle of handles) {
				try {
					files.push(await handle.getFile());
				} catch {}
			}
			if (files.length) await handleIntent({ kind: 'importFiles', importFiles: { files } });
		});
	});

	$effect(() => {
		if (typeof window === 'undefined') return;
		const handleHomeAction = (event: Event) => {
			if ($currentWorkspace) return;
			switch (
				(event as CustomEvent<'newChat' | 'closeTab' | 'nextTab' | 'prevTab' | 'toggleSplit'>)
					.detail
			) {
				case 'newChat':
					openHomeChat();
					break;
				case 'closeTab':
					if (
						activeHomeGroup &&
						!activeHomeGroup.tabs.find((tab) => tab.id === activeHomeGroup.activeTabId)?.permanent
					) {
						closeHomeTab(activeHomeGroup.activeTabId, activeHomeGroup.id);
					}
					break;
				case 'nextTab':
					cycleHomeTab(1);
					break;
				case 'prevTab':
					cycleHomeTab(-1);
					break;
				case 'toggleSplit':
					toggleHomeSplit();
					break;
			}
		};
		window.addEventListener('cptr:home-action', handleHomeAction);
		return () => window.removeEventListener('cptr:home-action', handleHomeAction);
	});

	// Sidebar "new chat" / Default workspace chat clicks land in the Home chat slot.
	$effect(() => {
		const request = $homeChatRequest;
		if (!request || $currentWorkspace) return;
		untrack(() => {
			homeChatRequest.set(null);
			openHomeChat(request.chatId);
		});
	});

	// ── Draggable divider ──────────────────────────────────────────

	let resizingSplit = $state<{
		splitId: string;
		direction: SplitDirection;
		element: HTMLElement;
	} | null>(null);

	function handleDividerPointerDown(
		e: PointerEvent,
		split: Extract<EditorLayout, { type: 'split' }>
	) {
		e.preventDefault();
		const divider = e.currentTarget as HTMLElement;
		resizingSplit = {
			splitId: split.id,
			direction: split.direction,
			element: divider.parentElement!
		};
		divider.setPointerCapture(e.pointerId);
	}

	function handleDividerPointerMove(e: PointerEvent) {
		if (!resizingSplit) return;
		const rect = resizingSplit.element.getBoundingClientRect();

		let ratio: number;
		if (resizingSplit.direction === 'horizontal') {
			ratio = (e.clientX - rect.left) / rect.width;
		} else {
			ratio = (e.clientY - rect.top) / rect.height;
		}
		if ($currentWorkspace) setSplitRatio(resizingSplit.splitId, ratio);
		else setHomeSplitRatio(resizingSplit.splitId, ratio);
	}

	function handleDividerPointerUp() {
		resizingSplit = null;
	}

	// Computed
	let isWideScreen = $state(typeof window !== 'undefined' ? window.innerWidth >= 1024 : false);

	$effect(() => {
		if (typeof window === 'undefined') return;
		function onResize() {
			isWideScreen = window.innerWidth >= 1024;
		}
		window.addEventListener('resize', onResize);
		return () => window.removeEventListener('resize', onResize);
	});

	const allGroups = $derived($currentWorkspace?.groups ?? []);
	const layout = $derived($currentWorkspace?.layout ?? null);
	const activeGroup = $derived(
		allGroups.find((group) => group.id === $currentWorkspace?.activeGroupId) ?? allGroups[0] ?? null
	);

	function getGroupActiveTab(group: EditorGroup): Tab | null {
		return group.tabs.find((t) => t.id === group.activeTabId) ?? null;
	}

	// ── Drag-to-split ─────────────────────────────────────────────

	const SPLIT_EDGE_FRACTION = 0.3;

	type SplitDropZone = 'left' | 'right' | 'top' | 'bottom';
	type TabDragPayload = { tabId: string; groupId: string };

	let dragOverZone = $state<{ groupId: string; zone: SplitDropZone } | null>(null);

	$effect(() => {
		const clearSplitPreview = () => (dragOverZone = null);
		document.addEventListener('dragend', clearSplitPreview, true);
		document.addEventListener('drop', clearSplitPreview, true);
		window.addEventListener('blur', clearSplitPreview);
		return () => {
			document.removeEventListener('dragend', clearSplitPreview, true);
			document.removeEventListener('drop', clearSplitPreview, true);
			window.removeEventListener('blur', clearSplitPreview);
		};
	});

	function hasTabDrag(dataTransfer: DataTransfer): boolean {
		return dataTransfer.types.includes(TAB_DRAG_MIME) || dataTransfer.types.includes('text/tab-id');
	}

	function readTabDragPayload(dataTransfer: DataTransfer): TabDragPayload | null {
		const raw = dataTransfer.getData(TAB_DRAG_MIME);
		if (raw) {
			try {
				const parsed = JSON.parse(raw) as Partial<TabDragPayload>;
				if (typeof parsed.tabId === 'string' && typeof parsed.groupId === 'string') {
					return { tabId: parsed.tabId, groupId: parsed.groupId };
				}
			} catch {
				// Fall through to the legacy payload below.
			}
		}

		const tabId = dataTransfer.getData('text/tab-id');
		const groupId = dataTransfer.getData('text/group-id');
		return tabId && groupId ? { tabId, groupId } : null;
	}

	function getSplitDropZone(e: DragEvent, pane: HTMLElement): SplitDropZone | null {
		if (!isWideScreen || !e.dataTransfer) return null;
		if (e.dataTransfer.types.includes('Files') || !hasTabDrag(e.dataTransfer)) return null;

		const rect = pane.getBoundingClientRect();
		if (
			e.clientX < rect.left ||
			e.clientX > rect.right ||
			e.clientY < rect.top ||
			e.clientY > rect.bottom
		) {
			return null;
		}

		const horizontalBand = rect.width * SPLIT_EDGE_FRACTION;
		const verticalBand = rect.height * SPLIT_EDGE_FRACTION;
		const leftDistance = e.clientX - rect.left;
		const rightDistance = rect.right - e.clientX;
		const topDistance = e.clientY - rect.top;
		const bottomDistance = rect.bottom - e.clientY;
		const candidates: { zone: SplitDropZone; distance: number }[] = [];

		if (leftDistance <= horizontalBand) candidates.push({ zone: 'left', distance: leftDistance });
		if (rightDistance <= horizontalBand)
			candidates.push({ zone: 'right', distance: rightDistance });
		if (topDistance <= verticalBand) candidates.push({ zone: 'top', distance: topDistance });
		if (bottomDistance <= verticalBand)
			candidates.push({ zone: 'bottom', distance: bottomDistance });

		candidates.sort((a, b) => a.distance - b.distance);
		return candidates[0]?.zone ?? null;
	}

	function handlePaneDragOver(e: DragEvent, groupId: string) {
		const zone = getSplitDropZone(e, e.currentTarget as HTMLElement);
		if (!zone) {
			if (dragOverZone?.groupId === groupId) dragOverZone = null;
			return;
		}

		e.preventDefault();
		e.dataTransfer!.dropEffect = 'move';
		dragOverZone = { groupId, zone };
	}

	function handlePaneDragLeave(e: DragEvent, groupId: string) {
		if (
			!(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node) &&
			dragOverZone?.groupId === groupId
		) {
			dragOverZone = null;
		}
	}

	function handlePaneDrop(e: DragEvent, targetGroupId: string) {
		const zone =
			dragOverZone?.groupId === targetGroupId
				? dragOverZone.zone
				: getSplitDropZone(e, e.currentTarget as HTMLElement);

		if (!zone || !e.dataTransfer) {
			dragOverZone = null;
			return;
		}

		const payload = readTabDragPayload(e.dataTransfer);
		if (!payload) {
			dragOverZone = null;
			return;
		}

		e.preventDefault();
		const direction: SplitDirection =
			zone === 'left' || zone === 'right' ? 'horizontal' : 'vertical';
		const placement = zone === 'left' || zone === 'top' ? 'before' : 'after';
		if ($currentWorkspace) {
			moveTabToNewSplit(payload.tabId, payload.groupId, targetGroupId, direction, placement);
		} else {
			moveHomeTabToNewSplit(payload.tabId, payload.groupId, targetGroupId, direction, placement);
		}
		dragOverZone = null;
	}
</script>

{#if !$currentWorkspace}
	{#snippet renderHomePane(homePane: EditorGroup)}
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div
			class="split-pane"
			onpointerdown={() => setHomeActiveGroup(homePane.id)}
			onfocusin={() => setHomeActiveGroup(homePane.id)}
			ondragover={(event) => handlePaneDragOver(event, homePane.id)}
			ondragleave={(event) => handlePaneDragLeave(event, homePane.id)}
			ondrop={(event) => handlePaneDrop(event, homePane.id)}
		>
			<GroupTabBar
				group={homePane}
				home
				isPrimary={homePane.id === $homeState.groups[0]?.id}
				canClose={$homeState.groups.length > 1}
				homeActive={homePane.id === $homeState.activeGroupId}
				homeSplitActive={$homeState.groups.length > 1}
				homeSplitDirection={$homeState.splitDirection}
				onHomeSelect={(tabId) =>
					updateHomeTabs(homePane.id, (tabs) => ({ tabs, activeTabId: tabId }))}
				onHomeClose={(tabId) => closeHomeTab(tabId, homePane.id)}
				onHomeReorder={(oldIndex, newIndex) => reorderHomeTabs(homePane.id, oldIndex, newIndex)}
				onHomeMove={(tabId, fromGroupId) => moveHomeTabToGroup(tabId, fromGroupId, homePane.id)}
				onHomeNewChat={() => openHomeChatTab(homePane.id)}
				onHomeSplit={(direction) => {
					setHomeActiveGroup(homePane.id);
					splitHomeTab(direction);
				}}
				onHomeCloseGroup={() => closeHomeGroup(homePane.id)}
				onTabDragOver={() => {
					if (dragOverZone?.groupId === homePane.id) dragOverZone = null;
				}}
			/>
			<div class="pane-content">
				{#each homePane.tabs.filter((tab) => tab.type === 'home') as tab (tab.id)}
					<div class="persisted-tab" class:persisted-tab-hidden={tab.id !== homePane.activeTabId}>
						{#key homeChatKey}
							<ChatPanel
								chatId={tab.path?.startsWith('pending-') ? undefined : tab.path}
								tabId={tab.id}
								workspace={homeChatWorkspace}
								active={tab.id === homePane.activeTabId}
								onworkspacechange={tab.path ? undefined : (path) => (homeChatWorkspace = path)}
								ontabupdate={(tabId, chatId, label) =>
									updateHomeChatTab(tabId, chatId, label, homePane.id)}
								onopenchat={(chatId) => openHomeChat(chatId)}
							/>
						{/key}
					</div>
				{/each}
				{#each homePane.tabs.filter((tab) => tab.type === 'chat') as tab (tab.id)}
					<div class="persisted-tab" class:persisted-tab-hidden={tab.id !== homePane.activeTabId}>
						<ChatPanel
							chatId={tab.path?.startsWith('new-') || tab.path?.startsWith('pending-')
								? undefined
								: tab.path}
							tabId={tab.id}
							active={tab.id === homePane.activeTabId}
							ontabupdate={(tabId, chatId, label) =>
								updateHomeChatTab(tabId, chatId, label, homePane.id)}
							onopenchat={(chatId) => openHomeChat(chatId)}
						/>
					</div>
				{/each}
				{#each homePane.tabs.filter((tab) => tab.type === 'file' && tab.filePath) as tab (tab.id)}
					<div class="persisted-tab" class:persisted-tab-hidden={tab.id !== homePane.activeTabId}>
						<FileEditor
							filePath={tab.filePath!}
							tabId={tab.id}
							edit={tab.edit === true}
							searchTarget={tab.searchTarget}
						/>
					</div>
				{/each}
			</div>
			{#if dragOverZone?.groupId === homePane.id}
				<div class={`split-drop-zone split-drop-${dragOverZone.zone}`}></div>
			{/if}
		</div>
	{/snippet}

	{#snippet renderHomeLayout(node: EditorLayout)}
		{#if node.type === 'group'}
			{@const group = $homeState.groups.find((item) => item.id === node.groupId)}
			{#if group}{@render renderHomePane(group)}{/if}
		{:else}
			<div
				class="split-branch"
				class:split-branch-horizontal={node.direction === 'horizontal'}
				class:split-branch-vertical={node.direction === 'vertical'}
			>
				<div class="split-branch-child" style={`flex: ${node.ratio} 1 0%;`}>
					{@render renderHomeLayout(node.first)}
				</div>
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div
					class="split-divider"
					class:split-divider-h={node.direction === 'horizontal'}
					class:split-divider-v={node.direction === 'vertical'}
					onpointerdown={(event) => handleDividerPointerDown(event, node)}
					onpointermove={handleDividerPointerMove}
					onpointerup={handleDividerPointerUp}
					onpointercancel={handleDividerPointerUp}
				></div>
				<div class="split-branch-child" style={`flex: ${1 - node.ratio} 1 0%;`}>
					{@render renderHomeLayout(node.second)}
				</div>
			</div>
		{/if}
	{/snippet}

	<div class="split-container" class:is-dragging={resizingSplit !== null} role="presentation">
		{#if isWideScreen}
			{@render renderHomeLayout($homeState.layout)}
		{:else if activeHomeGroup}
			{@render renderHomePane(activeHomeGroup)}
		{/if}
	</div>
{:else}
	<!-- Editor groups layout -->
	<div class="split-container" class:is-dragging={resizingSplit !== null} role="presentation">
		{#if isWideScreen && layout}
			{@render renderLayout(layout)}
		{:else if activeGroup}
			{@render renderPane(activeGroup)}
		{/if}
	</div>
{/if}

{#snippet renderLayout(node: EditorLayout)}
	{#if node.type === 'group'}
		{@const group = allGroups.find((item) => item.id === node.groupId)}
		{#if group}{@render renderPane(group)}{/if}
	{:else}
		<div
			class="split-branch"
			class:split-branch-horizontal={node.direction === 'horizontal'}
			class:split-branch-vertical={node.direction === 'vertical'}
		>
			<div class="split-branch-child" style={`flex: ${node.ratio} 1 0%;`}>
				{@render renderLayout(node.first)}
			</div>
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div
				class="split-divider"
				class:split-divider-h={node.direction === 'horizontal'}
				class:split-divider-v={node.direction === 'vertical'}
				onpointerdown={(event) => handleDividerPointerDown(event, node)}
				onpointermove={handleDividerPointerMove}
				onpointerup={handleDividerPointerUp}
				onpointercancel={handleDividerPointerUp}
			></div>
			<div class="split-branch-child" style={`flex: ${1 - node.ratio} 1 0%;`}>
				{@render renderLayout(node.second)}
			</div>
		</div>
	{/if}
{/snippet}

{#snippet renderPane(group: EditorGroup)}
	{@const groupTab = getGroupActiveTab(group)}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="split-pane"
		onpointerdown={() => setActiveGroup(group.id)}
		onfocusin={() => setActiveGroup(group.id)}
		ondragover={(event) => handlePaneDragOver(event, group.id)}
		ondragleave={(event) => handlePaneDragLeave(event, group.id)}
		ondrop={(event) => handlePaneDrop(event, group.id)}
	>
		<GroupTabBar
			{group}
			canClose={allGroups.length > 1}
			isPrimary={group.id === allGroups[0]?.id}
			onTabDragOver={() => {
				if (dragOverZone?.groupId === group.id) dragOverZone = null;
			}}
		/>
		<div class="pane-content">
			{#each group.tabs.filter((tab) => tab.type === 'files') as tab (tab.id)}
				<div class="persisted-tab" class:persisted-tab-hidden={tab.id !== group.activeTabId}>
					<FileBrowser />
				</div>
			{/each}
			{#each group.tabs.filter((tab) => tab.type === 'file' && tab.filePath) as tab (tab.id)}
				<div class="persisted-tab" class:persisted-tab-hidden={tab.id !== group.activeTabId}>
					<FileEditor
						filePath={tab.filePath!}
						tabId={tab.id}
						edit={tab.edit === true}
						searchTarget={tab.searchTarget}
					/>
				</div>
			{/each}
			{#each group.tabs.filter((tab) => tab.type === 'chat') as tab (tab.id)}
				<div class="persisted-tab" class:persisted-tab-hidden={tab.id !== group.activeTabId}>
					<ChatPanel
						workspace={$currentWorkspace!.path}
						chatId={tab.path?.startsWith('new-') || tab.path?.startsWith('pending-')
							? undefined
							: tab.path}
						tabId={tab.id}
						active={tab.id === group.activeTabId}
						onopenchat={(chatId) => openChatTab(chatId, group.id)}
					/>
				</div>
			{/each}
			{#if !groupTab}
				<FileBrowser />
			{/if}
			{#if $gitReviewOpen && group.id === allGroups[0]?.id}
				<div class="persisted-tab git-review-panel">
					<GitView />
				</div>
			{/if}
		</div>
		{#if dragOverZone?.groupId === group.id}
			<div class={`split-drop-zone split-drop-${dragOverZone.zone}`}></div>
		{/if}
	</div>
{/snippet}

{#if pendingIntent}
	<WorkspacePicker
		intent={pendingIntent}
		workspaces={$workspaceList}
		onchoose={chooseIntentWorkspace}
		oncancel={() => (pendingIntent = null)}
	/>
{/if}

{#if showPicker}
	<DirectoryPicker
		onclose={() => {
			showPicker = false;
			folderPickerIntent = null;
			folderPickerWorkspace = null;
		}}
		onselect={folderPickerIntent ? handlePickedImportFolder : undefined}
	/>
{/if}

<style>
	@reference "../app.css";

	/* Container */
	.split-container {
		display: flex;
		width: 100%;
		height: 100%;
		position: relative;
		overflow: hidden;
		background: var(--app-bg);
		color: var(--app-fg);
	}

	.split-branch {
		display: flex;
		flex: 1;
		min-width: 0;
		min-height: 0;
		overflow: hidden;
	}

	.split-branch-horizontal {
		flex-direction: row;
	}

	.split-branch-vertical {
		flex-direction: column;
	}

	.split-branch-child {
		display: flex;
		min-width: 0;
		min-height: 0;
		overflow: hidden;
	}

	/* Panes */
	.split-pane {
		display: flex;
		flex: 1;
		flex-direction: column;
		position: relative;
		overflow: hidden;
		min-width: 0;
		min-height: 0;
	}

	.pane-content {
		flex: 1;
		min-height: 0;
		min-width: 0;
		overflow: hidden;
		position: relative;
		background: var(--app-bg);
		color: var(--app-fg);
	}

	/* ── Divider ─────────────────────────────────────────────── */
	.split-divider {
		flex-shrink: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 10;
		position: relative;
		transition: background 0.15s;
	}

	.split-divider-h {
		width: 0.375rem;
		cursor: col-resize;
	}

	.split-divider-v {
		height: 0.375rem;
		cursor: row-resize;
	}

	.split-divider-handle {
		display: none;
	}

	.split-divider::before {
		content: '';
		position: absolute;
		z-index: -1;
	}

	.split-divider-h::before {
		width: 1px;
		height: 100%;
		left: 50%;
		transform: translateX(-50%);
		background: var(--app-divider);
	}

	:global(.dark) .split-divider-h::before {
		background: var(--app-divider);
	}

	.split-divider-v::before {
		height: 1px;
		width: 100%;
		top: 50%;
		transform: translateY(-50%);
		background: var(--app-divider);
	}

	:global(.dark) .split-divider-v::before {
		background: var(--app-divider);
	}

	.split-divider:hover,
	.is-dragging .split-divider {
		background: var(--app-active);
	}

	/* Drag state */
	.is-dragging {
		user-select: none;
	}

	/* ── Drop zones for drag-to-split ─────────────────── */
	.split-drop-zone {
		position: absolute;
		z-index: 15;
		display: block;
		flex: none;
		contain: layout paint;
		pointer-events: none;
		--split-tabbar-height: 2.25rem;
		background: color-mix(in oklab, var(--app-fg) 6%, transparent);
		box-shadow: inset 0 0 0 1px color-mix(in oklab, var(--app-fg) 16%, transparent);
	}

	.split-drop-left,
	.split-drop-right {
		top: var(--split-tabbar-height);
		bottom: 0;
		width: 50%;
	}

	.split-drop-left {
		left: 0;
	}

	.split-drop-right {
		right: 0;
	}

	.split-drop-top,
	.split-drop-bottom {
		left: 0;
		right: 0;
		height: calc((100% - var(--split-tabbar-height)) / 2);
	}

	.split-drop-top {
		top: var(--split-tabbar-height);
	}

	.split-drop-bottom {
		bottom: 0;
	}

	/* ── Persisted file editor tabs ────────────────────── */
	.persisted-tab {
		position: absolute;
		inset: 0;
		z-index: 1;
		overflow: hidden;
	}

	.persisted-tab-hidden {
		visibility: hidden;
		z-index: 0;
		pointer-events: none;
	}

	.git-review-panel {
		z-index: 2;
	}
</style>
