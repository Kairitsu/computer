<script lang="ts">
	import { onMount, onDestroy, tick } from 'svelte';
	import { mount, unmount } from 'svelte';
	import { Editor } from '@tiptap/core';
	import StarterKit from '@tiptap/starter-kit';
	import { Markdown } from '@tiptap/markdown';
	import Placeholder from '@tiptap/extension-placeholder';
	import CodeBlockLowlight from '@tiptap/extension-code-block-lowlight';
	import { all, createLowlight } from 'lowlight';

	import { createFileMention, extractMentionedFiles, type FileMentionAttrs } from './FileMention';
	import { createSlashCommandMention } from './SlashCommandMention';
	import FileSuggestionPopup from './FileSuggestionPopup.svelte';
	import { searchFiles } from '$lib/apis/files';
	import { uploadFile } from '$lib/apis/files';
	import type { ChatTask, ContextUsage, ReasoningEffort } from '$lib/apis/chat';
	import ModelHubMenu from './ModelHubMenu.svelte';
	import UsageIndicator from './UsageIndicator.svelte';
	import Popover from '../common/Popover.svelte';
	import { chatModels } from '$lib/stores/chat';
	import { workspaceList } from '$lib/stores';
	import { getPathDisplayName } from '$lib/utils/paths';
	import SendButton from './SendButton.svelte';
	import PlusMenu from './PlusMenu.svelte';
	import QueuedMessageItem from './QueuedMessageItem.svelte';
	import Tasks from './Tasks.svelte';
	import AskUserCard from './AskUserCard.svelte';
	import PlanApprovalCard from './PlanApprovalCard.svelte';
	import Icon from '../Icon.svelte';
	import type { ToolApprovalMode } from '$lib/apis/chat';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { t } from '$lib/i18n';
	import { TAB_DRAG_MIME } from '$lib/constants';
	import { tooltip } from '$lib/tooltip';

	interface Props {
		inputText: string;
		selectedModel: string;
		toolApprovalMode?: ToolApprovalMode;
		planMode?: boolean;
		requestParams?: Record<string, unknown>;
		reasoningEffort?: ReasoningEffort | null;
		contextWindow?: number | null;
		sending: boolean;
		streaming?: boolean;
		workspace?: string;
		placeholder?: string;
		contextUsage?: ContextUsage | null;
		tasks?: ChatTask[];
		askUser?: any;
		planApproval?: boolean;
		onplanapprove?: () => void;
		onplanrevise?: () => void;
		queuedMessages?: { id: string; content: string }[];
		hasChatContent?: boolean;
		onsend: () => void;
		onfork?: () => void;
		onplan?: () => void;
		onstatus?: () => void;
		oncancel?: () => void;
		onaskuseranswer?: (
			messageId: string,
			callId: string,
			answers: Record<string, string>,
			timedOut: boolean
		) => void | Promise<void>;
		onqueuesendnow?: (id: string) => void;
		onqueueedit?: (id: string) => void;
		onqueuedelete?: (id: string) => void;
		onsettingschange?: () => void;
		ontoolapprovalchange?: (mode: ToolApprovalMode) => void;
		/** New-chat landing only: pick the workspace the chat will belong to. */
		onworkspacechange?: (path: string) => void;
	}
	let {
		inputText = $bindable(),
		selectedModel = $bindable(),
		toolApprovalMode = $bindable('auto'),
		planMode = $bindable(false),
		requestParams = $bindable({}),
		reasoningEffort = $bindable(null),
		contextWindow = $bindable(null),
		sending,
		streaming = false,
		workspace = '',
		placeholder = 'Message...',
		contextUsage = null,
		tasks = [],
		askUser = null,
		planApproval = false,
		onplanapprove,
		onplanrevise,
		queuedMessages = [],
		hasChatContent = false,
		onsend,
		onfork,
		onplan,
		onstatus,
		oncancel,
		onaskuseranswer,
		onqueuesendnow,
		onqueueedit,
		onqueuedelete,
		onsettingschange,
		ontoolapprovalchange,
		onworkspacechange
	}: Props = $props();

	let editorEl: HTMLDivElement | undefined = $state();
	let editor: Editor | null = $state(null);
	let selectedSlashCommandIndex = $state(0);
	let modelSelector: ModelHubMenu | undefined = $state();
	let workspaceChipEl: HTMLButtonElement | undefined = $state();
	let approvalChipEl: HTMLButtonElement | undefined = $state();
	let workspaceMenuOpen = $state(false);
	let approvalMenuOpen = $state(false);
	const selectedChatModel = $derived($chatModels.find((model) => model.id === selectedModel));
	// Grok's auto mode is its auto-review: risky calls are refused rather than asked about.
	const grokSelected = $derived(selectedChatModel?.agent_id === 'grok');
	const approvalModes = $derived([
		{
			value: 'ask' as ToolApprovalMode,
			label: $t('plusMenu.askApproval'),
			desc: $t('plusMenu.askApprovalDesc')
		},
		{
			value: 'auto' as ToolApprovalMode,
			label: $t(grokSelected ? 'plusMenu.grokAutoReview' : 'plusMenu.autoApprove'),
			desc: $t(grokSelected ? 'plusMenu.grokAutoReviewDesc' : 'plusMenu.autoApproveDesc')
		},
		{
			value: 'full' as ToolApprovalMode,
			label: $t('plusMenu.fullAccess'),
			desc: $t('plusMenu.fullAccessDesc')
		}
	]);
	const approvalLabel = $derived(
		approvalModes.find((mode) => mode.value === toolApprovalMode)?.label ??
			$t('plusMenu.toolPermissions')
	);
	const workspaceLabel = $derived(
		workspace ? getPathDisplayName(workspace, 'workspace') : $t('sidebar.defaultWorkspace')
	);
	const modelContextWindow = $derived(selectedChatModel?.context_window);
	// A window the agent CLI does not offer is not sent to it, so the CLI default applies.
	const usageContextWindow = $derived(
		contextWindow &&
			selectedChatModel?.context_windows?.length &&
			!selectedChatModel.context_windows.includes(contextWindow)
			? null
			: contextWindow
	);

	function selectApprovalMode(mode: ToolApprovalMode) {
		approvalMenuOpen = false;
		toolApprovalMode = mode;
		if (ontoolapprovalchange) ontoolapprovalchange(mode);
		else onsettingschange?.();
	}

	function selectWorkspace(path: string) {
		workspaceMenuOpen = false;
		onworkspacechange?.(path);
	}
	function isMobileInput(): boolean {
		return (
			typeof window !== 'undefined' &&
			window.matchMedia('(hover: none) and (pointer: coarse)').matches
		);
	}

	// ── Lowlight setup ──────────────────────────────
	const lowlight = createLowlight(all);
	const _origHighlight = lowlight.highlight.bind(lowlight);
	lowlight.highlight = (lang: string, value: string, opts?: Record<string, unknown>) => {
		if (!lowlight.registered(lang)) return lowlight.highlightAuto(value);
		return _origHighlight(lang, value, opts);
	};

	// ── File Uploads ────────────────────────────────
	let attachedUploads = $state<
		{ id: string; name: string; url: string; type: string; loading?: boolean }[]
	>([]);
	let isDragging = $state(false);

	function isTabDrag(e: DragEvent): boolean {
		return Boolean(
			e.dataTransfer?.types.includes(TAB_DRAG_MIME) || e.dataTransfer?.types.includes('text/tab-id')
		);
	}

	async function processFiles(files: File[]) {
		for (const file of files) {
			const id = Math.random().toString(36).substring(7);
			const isImage = file.type.startsWith('image/');
			const type = isImage ? 'image' : 'file';
			attachedUploads = [...attachedUploads, { id, name: file.name, url: '', type, loading: true }];

			try {
				const form = new FormData();
				form.append('file', file);
				const res = await uploadFile(form);
				if (res && res.id) {
					attachedUploads = attachedUploads.map((u) =>
						u.id === id ? { ...u, id: res.id, url: res.url, loading: false } : u
					);
				} else {
					attachedUploads = attachedUploads.filter((u) => u.id !== id);
				}
			} catch (err) {
				console.error('Upload failed', err);
				attachedUploads = attachedUploads.filter((u) => u.id !== id);
			}
		}
	}

	function handleDrop(e: DragEvent) {
		if (isTabDrag(e)) return;

		e.preventDefault();
		isDragging = false;
		if (e.dataTransfer?.files) {
			processFiles(Array.from(e.dataTransfer.files));
		}
	}

	function handlePaste(e: ClipboardEvent) {
		if (e.clipboardData?.items) {
			const files: File[] = [];
			for (const item of Array.from(e.clipboardData.items)) {
				if (item.kind === 'file') {
					const file = item.getAsFile();
					if (file) files.push(file);
				}
			}
			if (files.length > 0) {
				e.preventDefault(); // Stop TipTap from inserting base64 strings
				processFiles(files);
			}
		}
	}

	function removeUpload(id: string) {
		attachedUploads = attachedUploads.filter((u) => u.id !== id);
	}

	// ── @file mention suggestion ────────────────────
	let popupEl: HTMLDivElement | null = null;
	let popupComponent: Record<string, any> | null = null;
	let activeClientRectFn: (() => DOMRect | null) | null = null;
	let repositionRafId: number | null = null;

	async function fetchSuggestions({ query }: { query: string }): Promise<FileMentionAttrs[]> {
		if (!workspace) return [];
		try {
			const data = await searchFiles(query || '', workspace);
			const results = (data as any).results ?? [];
			return results.slice(0, 10).map((r: any) => ({
				id: r.type === 'directory' && !r.path.endsWith('/') ? r.path + '/' : r.path,
				label: r.name,
				type: r.type === 'directory' ? 'directory' : 'file'
			}));
		} catch {
			return [];
		}
	}

	function mountPopup(
		items: FileMentionAttrs[],
		selectedIdx: number,
		onselect: (i: number) => void
	) {
		// Destroy previous instance
		if (popupComponent) {
			try {
				unmount(popupComponent);
			} catch {}
			popupComponent = null;
		}
		if (!popupEl) {
			popupEl = document.createElement('div');
			document.body.appendChild(popupEl);
		}
		popupComponent = mount(FileSuggestionPopup, {
			target: popupEl,
			props: { items, selectedIndex: selectedIdx, onselect }
		});
	}

	function startRepositionLoop() {
		stopRepositionLoop();
		function tick() {
			if (activeClientRectFn) {
				updatePopupPosition(activeClientRectFn());
				repositionRafId = requestAnimationFrame(tick);
			}
		}
		repositionRafId = requestAnimationFrame(tick);
	}

	function stopRepositionLoop() {
		if (repositionRafId !== null) {
			cancelAnimationFrame(repositionRafId);
			repositionRafId = null;
		}
	}

	function createSuggestionRenderer() {
		let selectedIndex = 0;
		let currentItems: FileMentionAttrs[] = [];
		let command: ((attrs: FileMentionAttrs) => void) | null = null;

		function doSelect(index: number) {
			const item = currentItems[index];
			if (item && command) command(item);
		}

		function remount() {
			mountPopup(currentItems, selectedIndex, doSelect);
		}

		return {
			onStart(props: any) {
				command = props.command;
				currentItems = props.items;
				selectedIndex = 0;
				activeClientRectFn = props.clientRect ?? null;
				remount();
				updatePopupPosition(props.clientRect?.());
				startRepositionLoop();
			},
			onUpdate(props: any) {
				command = props.command;
				currentItems = props.items;
				selectedIndex = 0;
				activeClientRectFn = props.clientRect ?? null;
				remount();
				updatePopupPosition(props.clientRect?.());
			},
			onKeyDown({ event }: { event: KeyboardEvent }) {
				if (event.key === 'ArrowDown') {
					selectedIndex = (selectedIndex + 1) % Math.max(currentItems.length, 1);
					remount();
					return true;
				}
				if (event.key === 'ArrowUp') {
					selectedIndex =
						(selectedIndex - 1 + currentItems.length) % Math.max(currentItems.length, 1);
					remount();
					return true;
				}
				if (event.key === 'Enter') {
					if (isMobileInput()) return false;
					const item = currentItems[selectedIndex];
					if (item && command) command(item);
					return true;
				}
				if (event.key === 'Escape') {
					destroyPopup();
					return true;
				}
				return false;
			},
			onExit() {
				destroyPopup();
			}
		};
	}

	function updatePopupPosition(rect: DOMRect | null) {
		if (!popupEl || !rect) return;
		const child = popupEl.firstElementChild as HTMLElement | null;
		if (!child) return;
		const popupHeight = child.offsetHeight || 200;
		child.style.position = 'fixed';
		child.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - 340))}px`;
		child.style.top = `${rect.top - popupHeight - 8}px`;
	}

	function destroyPopup() {
		stopRepositionLoop();
		activeClientRectFn = null;
		if (popupComponent) {
			try {
				unmount(popupComponent);
			} catch {}
			popupComponent = null;
		}
		if (popupEl) {
			popupEl.remove();
			popupEl = null;
		}
	}

	// ── /command suggestion state ───────────────────
	let activeSlashRange = $state<{ from: number; to: number } | null>(null);
	type SlashSuggestionItem = { kind: 'command'; id: string };
	let slashSuggestionItems = $state<SlashSuggestionItem[]>([]);
	let slashCommandsEl: HTMLDivElement | undefined = $state();

	// ── /command suggestion ─────────────────────────
	function clearSlashSuggestionState() {
		activeSlashRange = null;
		slashSuggestionItems = [];
	}

	function scrollSelectedSlashCommandIntoView() {
		const selected = slashCommandsEl?.querySelector('.app-interactive-active');
		selected?.scrollIntoView({ block: 'nearest' });
	}

	function normalizeSlashIndex(index: number, length = slashSuggestionItems.length) {
		if (!length) return 0;
		return (index + length) % length;
	}

	function selectSlashIndex(index: number, length = slashSuggestionItems.length) {
		const nextIndex = normalizeSlashIndex(index, length);
		selectedSlashCommandIndex = nextIndex;
		void tick().then(scrollSelectedSlashCommandIntoView);
		return nextIndex;
	}

	function createSlashSuggestionRenderer() {
		let selectedIndex = 0;
		let currentItems: SlashSuggestionItem[] = [];

		function updateState(props: any) {
			activeSlashRange = props.range ?? null;
			currentItems = props.items ?? [];
			slashSuggestionItems = currentItems;
			selectedIndex = selectSlashIndex(0);
		}

		return {
			onStart: updateState,
			onUpdate: updateState,
			onKeyDown({ event }: { event: KeyboardEvent }) {
				if (!currentItems.length) return false;
				if (event.key === 'ArrowDown') {
					event.preventDefault();
					selectedIndex = normalizeSlashIndex(selectedIndex + 1, currentItems.length);
					selectSlashIndex(selectedIndex, currentItems.length);
					return true;
				}
				if (event.key === 'ArrowUp') {
					event.preventDefault();
					selectedIndex = normalizeSlashIndex(selectedIndex - 1, currentItems.length);
					selectSlashIndex(selectedIndex, currentItems.length);
					return true;
				}
				if (event.key === 'Enter') {
					event.preventDefault();
					selectedIndex = normalizeSlashIndex(selectedIndex, currentItems.length);
					runSlashSuggestionItem(currentItems[selectedIndex]);
					return true;
				}
				if (event.key === 'Escape') {
					clearSlashSuggestionState();
					return true;
				}
				return false;
			},
			onExit: clearSlashSuggestionState
		};
	}

	// ── Editor lifecycle ────────────────────────────
	onMount(() => {
		if (!editorEl) return;

		const fileMention = createFileMention({
			items: fetchSuggestions,
			render: createSuggestionRenderer
		});

		const slashCommandMention = createSlashCommandMention({
			items: fetchSlashSuggestions,
			render: createSlashSuggestionRenderer
		});

		editor = new Editor({
			element: editorEl,
			extensions: [
				StarterKit.configure({
					codeBlock: false,
					heading: { levels: [1, 2, 3] }
				}),
				Markdown,
				Placeholder.configure({ placeholder }),
				CodeBlockLowlight.configure({ lowlight }),
				fileMention,
				slashCommandMention
			],
			content: inputText || '',
			contentType: inputText ? 'markdown' : undefined,
			autofocus: true,
			editorProps: {
				attributes: {
					class: 'chat-prosemirror',
					spellcheck: 'true'
				},
				handleKeyDown: (view, event) => {
					if (event.key === 'Enter' && isMobileInput()) return false;

					if (event.key === 'Enter' && !event.shiftKey) {
						// Don't send while suggestion popup is open — let it confirm selection
						if (popupComponent) return false;
						const { state } = view;
						const head = state.selection.$head;

						let inside = false;
						for (let d = head.depth; d > 0; d--) {
							const name = head.node(d).type.name;
							if (['codeBlock', 'bulletList', 'orderedList', 'listItem'].includes(name)) {
								inside = true;
								break;
							}
						}
						if (!inside) {
							event.preventDefault();
							handleSubmit();
							return true;
						}
					}
					return false;
				}
			},
			onUpdate: ({ editor: e }) => {
				inputText = (e as any).getMarkdown() || '';
			}
		});
	});

	onDestroy(() => {
		destroyPopup();
		editor?.destroy();
		editor = null;
	});

	// Sync external inputText changes (e.g. cleared after send)
	$effect(() => {
		if (!editor || editor.isDestroyed) return;
		const editorMd = (editor as any).getMarkdown() || '';
		if (inputText !== editorMd) {
			if (inputText === '') {
				editor.commands.clearContent();
			} else {
				editor.commands.setContent(inputText);
			}
		}
	});

	export function focus() {
		editor?.commands.focus();
	}

	export function resetHeight() {
		// TipTap auto-sizes; no-op kept for API compat
	}

	export function getFiles(): any[] {
		return attachedUploads.filter((u) => !u.loading);
	}

	export function clearUploads() {
		attachedUploads = [];
	}

	function getSlashCommandIds(query: string) {
		const slashCommandQuery = `/${query}`.toLowerCase();
		const ids: string[] = [];
		if (onplan && '/plan'.startsWith(slashCommandQuery)) ids.push('plan');
		if (hasChatContent && onfork && '/fork'.startsWith(slashCommandQuery)) ids.push('fork');
		if (hasChatContent && onstatus && '/status'.startsWith(slashCommandQuery)) ids.push('status');
		if ('/model'.startsWith(slashCommandQuery)) ids.push('model');
		return ids;
	}

	async function fetchSlashSuggestions({
		query
	}: {
		query: string;
	}): Promise<SlashSuggestionItem[]> {
		if (/\s/.test(query)) return [];
		return getSlashCommandIds(query).map((id) => ({ kind: 'command' as const, id }));
	}

	const slashCommandIds = $derived(slashSuggestionItems.map((item) => item.id));
	const slashSuggestionIds = $derived(slashSuggestionItems.map((item) => `command:${item.id}`));
	const showSlashCommands = $derived(slashSuggestionIds.length > 0);

	$effect(() => {
		if (selectedSlashCommandIndex >= slashSuggestionIds.length) selectedSlashCommandIndex = 0;
	});

	function selectedSlashCommand(commandId: string) {
		return slashSuggestionIds[selectedSlashCommandIndex] === `command:${commandId}`;
	}

	function selectSlashCommand(commandId: string) {
		selectSlashIndex(slashSuggestionIds.indexOf(`command:${commandId}`));
	}

	function runSlashSuggestion(suggestionId: string | undefined) {
		runSlashCommand(suggestionId?.replace(/^command:/, ''));
	}

	function runSlashSuggestionItem(item: SlashSuggestionItem | undefined) {
		if (item) runSlashCommand(item.id);
	}

	function removeSlashCommandToken() {
		if (editor && !editor.isDestroyed && activeSlashRange) {
			editor.chain().focus().deleteRange(activeSlashRange).run();
		} else {
			inputText = inputText.replace(/(^|\s)\/\S*$/, '$1');
		}
		clearSlashSuggestionState();
	}

	function runSlashCommand(commandId: string | undefined) {
		if (commandId === 'fork' && (sending || streaming)) return;
		if (commandId === 'fork' && onfork) {
			removeSlashCommandToken();
			onfork();
			return;
		}
		if (commandId === 'plan' && onplan) {
			removeSlashCommandToken();
			onplan();
			return;
		}
		if (commandId === 'status' && onstatus) {
			removeSlashCommandToken();
			onstatus();
			return;
		}
		if (commandId === 'model') {
			removeSlashCommandToken();
			void modelSelector?.openSelector();
			return;
		}
		onsend();
	}

	function handleSubmit() {
		if (showSlashCommands) {
			runSlashSuggestion(slashSuggestionIds[selectedSlashCommandIndex]);
			return;
		}
		onsend();
	}

	async function focusAfterModelSelectorClose() {
		await tick();
		focus();
	}

	// Allow sending during streaming (message will be enqueued server-side)
	const canSend = $derived(!!(inputText.trim() && selectedModel && !sending));
</script>

<div
	class="relative {isDragging ? 'ring-2 ring-blue-500 rounded-3xl' : ''}"
	ondrop={handleDrop}
	onpaste={handlePaste}
	ondragover={(e) => {
		if (isTabDrag(e)) {
			isDragging = false;
			return;
		}
		e.preventDefault();
		isDragging = true;
	}}
	ondragleave={() => {
		isDragging = false;
	}}
	role="presentation"
>
	{#if askUser && onaskuseranswer}
		<div class="mx-1">
			<AskUserCard
				item={askUser.item}
				pairedOutput={askUser.output}
				chatId={askUser.chatId}
				messageId={askUser.messageId}
				onanswer={onaskuseranswer}
			/>
		</div>
	{/if}

	{#if planApproval && onplanapprove && onplanrevise}
		<div class="mx-1">
			<PlanApprovalCard onapprove={onplanapprove} onrevise={onplanrevise} />
		</div>
	{/if}

	{#if tasks.length > 0}
		<div class="mx-1">
			<Tasks {tasks} />
		</div>
	{/if}

	<!-- Queued messages (above input, matching open-webui layout) -->
	{#if queuedMessages.length > 0}
		<div
			class="app-subtle-surface mb-1 mx-2 py-0.5 px-1.5 rounded-2xl border overflow-x-hidden overflow-y-auto max-h-[25vh]"
		>
			{#each queuedMessages as qm (qm.id)}
				<QueuedMessageItem
					id={qm.id}
					content={qm.content}
					onsendnow={onqueuesendnow ?? (() => {})}
					onedit={onqueueedit ?? (() => {})}
					ondelete={onqueuedelete ?? (() => {})}
				/>
			{/each}
		</div>
	{/if}

	{#if showSlashCommands}
		<div
			bind:this={slashCommandsEl}
			class="app-theme app-surface absolute left-2 bottom-full mb-1 z-50 w-64 max-h-40 overflow-y-auto rounded-xl border shadow-xl p-0.5"
		>
			{#if slashCommandIds.length > 0}
				<div class="app-muted mb-0.5 px-2 pt-1 pb-0.5 text-[0.625rem] leading-none">
					{$t('chat.commands')}
				</div>
			{/if}
			{#if slashCommandIds.includes('plan')}
				<button
					type="button"
					aria-label={`${$t('chat.commandPlan')}: ${$t('chat.commandPlanDesc')}`}
					use:tooltip={{
						content: $t('chat.commandPlanDesc'),
						placement: 'top'
					}}
					class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-xs text-left transition-colors duration-75
						{selectedSlashCommand('plan') ? 'app-interactive-active' : ''}"
					onmousedown={(e) => e.preventDefault()}
					onclick={() => {
						runSlashCommand('plan');
					}}
					onmouseenter={() => selectSlashCommand('plan')}
				>
					<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
						<svg
							class="size-3.5"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="1.75"
							stroke-linecap="round"
							stroke-linejoin="round"
							aria-hidden="true"
						>
							<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
							<polyline points="14 2 14 8 20 8" />
							<line x1="9" y1="13" x2="15" y2="13" />
							<line x1="9" y1="17" x2="15" y2="17" />
						</svg>
					</span>
					<span class="flex-1 min-w-0 flex items-baseline gap-1.5 overflow-hidden">
						<span class="truncate">{$t('chat.commandPlan')}</span>
						<span class="app-muted text-[0.625rem] truncate shrink-0">
							{planMode ? $t('chat.commandPlanOn') : $t('chat.commandPlanOff')}
						</span>
					</span>
				</button>
			{/if}
			{#if slashCommandIds.includes('fork')}
				<button
					type="button"
					aria-label={`${$t('chat.commandFork')}: ${$t('chat.commandForkDesc')}`}
					use:tooltip={{
						content: $t('chat.commandForkDesc'),
						placement: 'top'
					}}
					class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-xs text-left transition-colors duration-75
						{selectedSlashCommand('fork') ? 'app-interactive-active' : ''} disabled:opacity-50"
					disabled={sending || streaming}
					onmousedown={(e) => e.preventDefault()}
					onclick={() => {
						runSlashCommand('fork');
					}}
					onmouseenter={() => selectSlashCommand('fork')}
				>
					<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
						<Icon name="chat-fork" size={14} strokeWidth={1.8} />
					</span>
					<span class="flex-1 min-w-0 flex items-baseline gap-1.5 overflow-hidden">
						<span class="truncate">{$t('chat.commandFork')}</span>
						<span class="app-muted text-[0.625rem] truncate shrink-0">
							{$t('chat.commandForkDesc')}
						</span>
					</span>
				</button>
			{/if}
			{#if slashCommandIds.includes('status')}
				<button
					type="button"
					aria-label={`${$t('chat.commandStatus')}: ${$t('chat.commandStatusDesc')}`}
					use:tooltip={{
						content: $t('chat.commandStatusDesc'),
						placement: 'top'
					}}
					class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-xs text-left transition-colors duration-75
						{selectedSlashCommand('status') ? 'app-interactive-active' : ''}"
					onmousedown={(e) => e.preventDefault()}
					onclick={() => {
						runSlashCommand('status');
					}}
					onmouseenter={() => selectSlashCommand('status')}
				>
					<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
						<svg
							class="size-3.5"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="1.75"
							stroke-linecap="round"
							stroke-linejoin="round"
							aria-hidden="true"
						>
							<path d="M12 14l4-4" />
							<path d="M3.34 19a10 10 0 1 1 17.32 0" />
						</svg>
					</span>
					<span class="flex-1 min-w-0 flex items-baseline gap-1.5 overflow-hidden">
						<span class="truncate">{$t('chat.commandStatus')}</span>
						<span class="app-muted text-[0.625rem] truncate shrink-0">
							{$t('chat.commandStatusDesc')}
						</span>
					</span>
				</button>
			{/if}
			{#if slashCommandIds.includes('model')}
				<button
					type="button"
					aria-label={`${$t('chat.commandModel')}: ${$t('chat.commandModelDesc')}`}
					use:tooltip={{
						content: $t('chat.commandModelDesc'),
						placement: 'top'
					}}
					class="slash-command-row flex items-center gap-2 w-full h-6 px-2 rounded-xl text-xs text-left transition-colors duration-75
						{selectedSlashCommand('model') ? 'app-interactive-active' : ''}"
					onmousedown={(e) => e.preventDefault()}
					onclick={() => {
						runSlashCommand('model');
					}}
					onmouseenter={() => selectSlashCommand('model')}
				>
					<span class="app-icon-muted flex items-center justify-center w-4 shrink-0">
						<Icon name="spark" size={13} strokeWidth={1.7} />
					</span>
					<span class="flex-1 min-w-0 flex items-baseline gap-1.5 overflow-hidden">
						<span class="truncate">{$t('chat.commandModel')}</span>
						<span class="app-muted text-[0.625rem] truncate shrink-0">/model</span>
					</span>
				</button>
			{/if}
		</div>
	{/if}

	<!-- Composer chips: workspace (left), usage + model hub (right) -->
	<div class="composer-chips mb-2 flex items-center gap-2 px-0.5" data-intro="chips">
		<button
			bind:this={workspaceChipEl}
			type="button"
			class="composer-chip min-w-0"
			class:static-chip={!onworkspacechange}
			disabled={!onworkspacechange}
			title={workspace || workspaceLabel}
			onclick={() => (workspaceMenuOpen = !workspaceMenuOpen)}
		>
			<Icon name={workspace ? 'folder' : 'home'} size={13} class="shrink-0" />
			<span class="truncate">{workspaceLabel}</span>
			{#if onworkspacechange}
				<Icon name="chevron-down" size={11} class="shrink-0 opacity-60" />
			{/if}
		</button>
		<div class="ml-auto flex min-w-0 items-center gap-1.5">
			<UsageIndicator
				{contextUsage}
				contextWindow={usageContextWindow}
				defaultContextWindow={modelContextWindow}
			/>
			<ModelHubMenu
				bind:this={modelSelector}
				bind:selectedModel
				bind:reasoningEffort
				bind:contextWindow
				onchange={onsettingschange}
				onclose={focusAfterModelSelectorClose}
			/>
		</div>
	</div>

	{#if workspaceMenuOpen && workspaceChipEl && onworkspacechange}
		<Popover anchor={workspaceChipEl} width="15rem" onclose={() => (workspaceMenuOpen = false)}>
			<button type="button" class="menu-option" onclick={() => selectWorkspace('')}>
				<Icon name="home" size={13} class="shrink-0" />
				<span class="flex-1 truncate text-left">{$t('sidebar.defaultWorkspace')}</span>
				{#if !workspace}<Icon name="check" size={12} class="shrink-0 menu-check" />{/if}
			</button>
			{#if $workspaceList.length > 0}
				<div class="app-divider h-px mx-1 my-0.5"></div>
				<div class="max-h-56 overflow-y-auto">
					{#each $workspaceList as ws (ws.path)}
						<button
							type="button"
							class="menu-option"
							title={ws.path}
							onclick={() => selectWorkspace(ws.path)}
						>
							<Icon name="folder" size={13} class="shrink-0" />
							<span class="flex-1 truncate text-left">{ws.name}</span>
							{#if workspace === ws.path}<Icon
									name="check"
									size={12}
									class="shrink-0 menu-check"
								/>{/if}
						</button>
					{/each}
				</div>
			{/if}
		</Popover>
	{/if}

	<div class="app-surface rounded-3xl shadow-lg border transition px-1" data-intro="composer">
		<!-- Uploaded Files Preview -->
		{#if attachedUploads.length > 0}
			<div class="mx-2 pt-2 flex flex-wrap gap-2">
				{#each attachedUploads as upload}
					<div class="relative group flex-shrink-0">
						{#if upload.loading}
							<div
								class="app-subtle-surface flex items-center justify-center size-8 rounded-xl border shadow-sm"
							>
								<Spinner size={16} />
							</div>
						{:else if upload.type === 'image'}
							<img
								src={upload.url}
								alt={upload.name}
								class="app-surface size-8 object-cover rounded-xl border shadow-sm"
							/>
						{:else}
							<div
								class="app-surface relative group px-2 w-48 h-8 flex items-center gap-1.5 border rounded-xl text-left flex-shrink-0 shadow-sm"
							>
								<div class="shrink-0">
									<Icon name="page-text" size={14} class="app-icon-muted" />
								</div>
								<div class="flex flex-col justify-center w-full overflow-hidden">
									<div class="text-xs flex justify-between items-center w-full gap-2">
										<div class="font-medium truncate flex-1">{upload.name}</div>
										<div class="app-muted text-[0.625rem] capitalize shrink-0">
											{upload.type === 'file' ? 'File' : upload.type}
										</div>
									</div>
								</div>
							</div>
						{/if}

						<div class="absolute -top-1.5 -right-1.5 z-10">
							<button
								class="app-surface border rounded-full group-hover:visible invisible transition outline-none shadow-sm flex items-center justify-center"
								type="button"
								onclick={() => removeUpload(upload.id)}
								aria-label={$t('chat.removeUpload')}
							>
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 20 20"
									fill="currentColor"
									class="size-3.5"
									aria-hidden="true"
								>
									<path
										d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"
									/>
								</svg>
							</button>
						</div>
					</div>
				{/each}
			</div>
		{/if}
		<!-- Editor area -->
		<div class="px-2.5">
			<div
				bind:this={editorEl}
				class="chat-editor-mount scrollbar-hidden"
				class:opacity-50={sending}
				class:pointer-events-none={sending}
			></div>
		</div>

		<!-- Toolbar. stopPropagation prevents TipTap from stealing focus -->
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div
			class="flex items-center justify-between mt-0.5 mb-2.5 mx-0.5"
			onmousedown={(e) => e.stopPropagation()}
		>
			<div class="ml-0.5 self-end flex items-center gap-1">
				<PlusMenu
					bind:planMode
					bind:requestParams
					onchange={onsettingschange}
					onfiles={(files) => {
						if (files) processFiles(Array.from(files));
					}}
					oncapture={(file) => {
						processFiles([file]);
					}}
				/>
				<button
					bind:this={approvalChipEl}
					type="button"
					class="approval-chip"
					class:danger={toolApprovalMode === 'full'}
					aria-haspopup="menu"
					aria-expanded={approvalMenuOpen}
					onclick={() => (approvalMenuOpen = !approvalMenuOpen)}
				>
					<Icon
						name={toolApprovalMode === 'full' ? 'warning-triangle' : 'shield'}
						size={13}
						class="shrink-0"
					/>
					<span class="truncate">{approvalLabel}</span>
					<Icon name="chevron-down" size={11} class="shrink-0 opacity-60" />
				</button>
				{#if approvalMenuOpen && approvalChipEl}
					<Popover anchor={approvalChipEl} width="15rem" onclose={() => (approvalMenuOpen = false)}>
						{#each approvalModes as mode}
							<button
								type="button"
								class="menu-option items-start py-1.5"
								onclick={() => selectApprovalMode(mode.value)}
							>
								<Icon
									name={mode.value === 'full' ? 'warning-triangle' : 'shield'}
									size={13}
									class="shrink-0 mt-px {mode.value === 'full' ? 'text-red-500' : ''}"
								/>
								<span class="flex-1 min-w-0 text-left">
									<span class="block">{mode.label}</span>
									<span class="block text-[0.625rem] leading-snug opacity-70">{mode.desc}</span>
								</span>
								{#if toolApprovalMode === mode.value}
									<Icon name="check" size={12} class="shrink-0 mt-px menu-check" />
								{/if}
							</button>
						{/each}
					</Popover>
				{/if}
				{#if planMode}
					<button
						type="button"
						aria-label={$t('chat.commandPlanOff')}
						title={$t('chat.commandPlanOff')}
						class="app-surface app-interactive group p-[0.3125rem] flex gap-1 items-center text-xs rounded-full transition-colors duration-150 border"
						onclick={() => onplan?.()}
					>
						<svg
							class="size-3 shrink-0 group-hover:hidden"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="1.75"
							stroke-linecap="round"
							stroke-linejoin="round"
						>
							<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
							<polyline points="14 2 14 8 20 8" />
							<line x1="9" y1="13" x2="15" y2="13" />
							<line x1="9" y1="17" x2="15" y2="17" />
						</svg>
						<svg
							class="size-3 shrink-0 hidden group-hover:block"
							viewBox="0 0 24 24"
							fill="none"
							stroke="currentColor"
							stroke-width="2"
							stroke-linecap="round"
							stroke-linejoin="round"
						>
							<line x1="18" y1="6" x2="6" y2="18" />
							<line x1="6" y1="6" x2="18" y2="18" />
						</svg>
					</button>
				{/if}
			</div>
			<div class="self-end mr-1 flex items-center gap-2">
				<SendButton {canSend} {streaming} onsend={handleSubmit} {oncancel} />
			</div>
		</div>
	</div>
</div>

<style>
	@reference "../../../app.css";

	/* ── Composer chips (Grok-style) ───────────── */
	.composer-chip {
		display: inline-flex;
		align-items: center;
		gap: 0.375rem;
		height: 1.875rem;
		max-width: 14rem;
		padding: 0 0.75rem;
		border-radius: 0.75rem;
		border: 1px solid color-mix(in oklab, var(--app-fg) 10%, transparent);
		background: var(--app-bg);
		color: var(--app-fg-muted);
		font-size: 0.75rem;
		box-shadow: 0 1px 2px color-mix(in oklab, black 5%, transparent);
		transition:
			border-color 0.1s,
			color 0.1s;
	}

	.composer-chip:hover:not(:disabled) {
		color: var(--app-fg);
		border-color: color-mix(in oklab, var(--app-fg) 20%, transparent);
	}

	.composer-chip.static-chip {
		cursor: default;
	}

	.approval-chip {
		display: inline-flex;
		align-items: center;
		gap: 0.3125rem;
		height: 1.5rem;
		max-width: 9rem;
		padding: 0 0.5rem;
		border-radius: 999px;
		font-size: 0.6875rem;
		font-weight: 500;
		color: var(--app-fg-subtle);
		transition:
			background 0.1s,
			color 0.1s;
	}

	.approval-chip:hover {
		background: var(--app-hover);
		color: var(--app-fg);
	}

	.approval-chip.danger {
		color: #dc2626;
	}

	:global(.dark) .approval-chip.danger {
		color: #f87171;
	}

	.menu-option {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		width: 100%;
		min-height: 1.875rem;
		padding: 0 0.5rem;
		border-radius: 0.5rem;
		font-size: 0.75rem;
		color: var(--app-fg-muted);
		transition:
			background 0.075s,
			color 0.075s;
	}

	.menu-option:hover {
		background: var(--app-hover);
		color: var(--app-fg);
	}

	.menu-option :global(.menu-check) {
		color: var(--app-accent);
	}

	/* ── ProseMirror editor ───────────────────────── */

	.chat-editor-mount :global(.chat-prosemirror) {
		@apply pt-2.5 pb-2 px-1 min-h-6 max-h-96 overflow-y-auto text-[0.8125rem] leading-relaxed outline-none break-words;
		font-size: 0.8125rem;
		color: var(--app-fg);
	}

	/* Placeholder */
	.chat-editor-mount :global(.chat-prosemirror p.is-editor-empty:first-child::before) {
		content: attr(data-placeholder);
		@apply float-left pointer-events-none h-0;
		color: color-mix(in oklab, var(--app-fg) 48%, var(--app-bg));
	}

	/* Paragraphs */
	.chat-editor-mount :global(.chat-prosemirror p) {
		@apply mb-1;
	}
	.chat-editor-mount :global(.chat-prosemirror p:last-child) {
		@apply mb-0;
	}

	/* Lists */
	.chat-editor-mount :global(.chat-prosemirror ul),
	.chat-editor-mount :global(.chat-prosemirror ol) {
		@apply mb-1 pl-4.5 text-sm;
	}
	.chat-editor-mount :global(.chat-prosemirror ul) {
		list-style-type: disc;
	}
	.chat-editor-mount :global(.chat-prosemirror ol) {
		list-style-type: decimal;
	}
	.chat-editor-mount :global(.chat-prosemirror li) {
		@apply my-0.5;
	}
	.chat-editor-mount :global(.chat-prosemirror li > p) {
		@apply mb-0.5;
	}

	/* Inline code */
	.chat-editor-mount :global(.chat-prosemirror code) {
		@apply rounded-sm px-1 py-px text-xs font-mono;
		background: color-mix(in oklab, var(--app-fg) 7%, transparent);
	}

	/* Code blocks */
	.chat-editor-mount :global(.chat-prosemirror pre) {
		@apply rounded-md px-3 py-2 overflow-x-auto my-1 text-xs font-mono;
		background: color-mix(in oklab, var(--app-fg) 5%, transparent);
	}
	.chat-editor-mount :global(.chat-prosemirror pre code) {
		@apply bg-transparent p-0 rounded-none text-inherit;
	}

	/* Blockquote */
	.chat-editor-mount :global(.chat-prosemirror blockquote) {
		@apply my-1 py-0.5 pl-3 border-l-2;
		border-color: color-mix(in oklab, var(--app-fg) 18%, transparent);
		color: color-mix(in oklab, var(--app-fg) 62%, var(--app-bg));
	}

	/* Strong */
	.chat-editor-mount :global(.chat-prosemirror strong) {
		@apply font-semibold;
	}

	/* Headings */
	.chat-editor-mount :global(.chat-prosemirror h1) {
		@apply text-base font-semibold my-1;
	}
	.chat-editor-mount :global(.chat-prosemirror h2) {
		@apply text-sm font-semibold my-1;
	}
	.chat-editor-mount :global(.chat-prosemirror h3) {
		@apply text-[0.8125rem] font-semibold my-1;
		font-size: 0.8125rem;
	}

	/* HR */
	.chat-editor-mount :global(.chat-prosemirror hr) {
		@apply border-none border-t my-2;
		border-color: color-mix(in oklab, var(--app-fg) 10%, transparent);
	}

	/* Syntax highlighting */
	.chat-editor-mount :global(.hljs-keyword) {
		color: #c678dd;
	}
	.chat-editor-mount :global(.hljs-string) {
		color: #98c379;
	}
	.chat-editor-mount :global(.hljs-number) {
		color: #d19a66;
	}
	.chat-editor-mount :global(.hljs-comment) {
		color: #5c6370;
		font-style: italic;
	}
	.chat-editor-mount :global(.hljs-function) {
		color: #61afef;
	}
	.chat-editor-mount :global(.hljs-title) {
		color: #61afef;
	}
	.chat-editor-mount :global(.hljs-built_in) {
		color: #e5c07b;
	}
</style>
