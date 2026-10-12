<script lang="ts">
	import {
		keybindings,
		ACTION_IDS,
		DEFAULT_KEYBINDINGS,
		formatChord,
		eventToChord,
		resetKeybindings
	} from '$lib/stores/keybindings';
	import type { ActionId } from '$lib/stores/keybindings';
	import KeyPill from '../KeyPill.svelte';
	import { t } from '$lib/i18n';

	/** Action descriptions for the second line. */
	const ACTION_DESCRIPTIONS: Record<ActionId, string> = $derived({
		newFile: $t('keyboard.newFile'),
		newChat: $t('keyboard.newChat'),
		closeTab: $t('keyboard.closeTab'),
		nextTab: $t('keyboard.nextTab'),
		prevTab: $t('keyboard.prevTab'),
		quickOpen: $t('keyboard.quickOpen'),
		searchAll: $t('keyboard.searchAll'),
		openSettings: $t('keyboard.openSettings'),
		toggleSplit: $t('keyboard.toggleSplit'),
		toggleSidebar: $t('keyboard.toggleSidebar'),
		approveToolCall: $t('keyboard.approveToolCall'),
		rejectToolCall: $t('keyboard.rejectToolCall')
	});

	/** Translated action labels for display. */
	const ACTION_LABELS: Record<ActionId, string> = $derived({
		newFile: $t('keyboard.action.newFile'),
		newChat: $t('keyboard.action.newChat'),
		closeTab: $t('keyboard.action.closeTab'),
		nextTab: $t('keyboard.action.nextTab'),
		prevTab: $t('keyboard.action.prevTab'),
		quickOpen: $t('keyboard.action.quickOpen'),
		searchAll: $t('keyboard.action.searchAll'),
		openSettings: $t('keyboard.action.openSettings'),
		toggleSplit: $t('keyboard.action.toggleSplit'),
		toggleSidebar: $t('keyboard.action.toggleSidebar'),
		approveToolCall: $t('keyboard.action.approveToolCall'),
		rejectToolCall: $t('keyboard.action.rejectToolCall')
	});

	let recordingAction = $state<ActionId | null>(null);

	function startRecording(actionId: ActionId) {
		recordingAction = actionId;
	}

	function cancelRecording() {
		recordingAction = null;
	}

	function handleKeydown(e: KeyboardEvent) {
		if (!recordingAction) return;
		e.preventDefault();
		e.stopPropagation();

		const chord = eventToChord(e);
		if (!chord) return; // Pure modifier press, keep waiting

		if (chord === 'ESCAPE') {
			cancelRecording();
			return;
		}

		// Save it
		keybindings.update((current) => ({
			...current,
			[recordingAction!]: chord
		}));
		recordingAction = null;
	}

	function clearBinding(actionId: ActionId) {
		keybindings.update((current) => ({
			...current,
			[actionId]: ''
		}));
	}

	/** Check if a chord is used by another action. */
	function getConflict(actionId: ActionId, chord: string): ActionId | null {
		if (!chord) return null;
		for (const id of ACTION_IDS) {
			if (id !== actionId && $keybindings[id] === chord) return id;
		}
		return null;
	}
</script>

<svelte:window onkeydown={handleKeydown} />

<div class="flex flex-col min-h-full">
	<div class="flex items-start justify-between gap-3 mb-3">
		<h2 class="text-sm font-medium text-gray-900 dark:text-white">{$t('keyboard.title')}</h2>
		<button type="button" class="settings-action shrink-0" onclick={resetKeybindings}
			>{$t('keyboard.resetDefaults')}</button
		>
	</div>

	<!-- Column headers -->
	<div class="flex items-center gap-2 mb-0.5 px-1">
		<span class="flex-1 text-[0.625rem] font-medium text-gray-400 dark:text-gray-600"
			>{$t('keyboard.command')}</span
		>
		<span
			class="w-[8.75rem] text-[0.625rem] font-medium text-gray-400 dark:text-gray-600 text-right"
			>{$t('keyboard.keybinding')}</span
		>
	</div>

	<div class="flex flex-col">
		{#each ACTION_IDS as actionId}
			{@const chord = $keybindings[actionId]}
			{@const isRecording = recordingAction === actionId}
			{@const conflict = getConflict(actionId, chord)}

			<div class="shortcut-row">
				<div class="flex-1 min-w-0">
					<div class="text-[0.71875rem] text-gray-700 dark:text-gray-300">
						{ACTION_LABELS[actionId]}
					</div>
					<div class="text-[0.625rem] text-gray-400 dark:text-gray-600 truncate leading-tight">
						{ACTION_DESCRIPTIONS[actionId]}
					</div>
				</div>

				<div class="w-[8.75rem] flex items-center justify-end shrink-0">
					{#if isRecording}
						<span class="recording-indicator">{$t('keyboard.pressKeys')}</span>
					{:else if chord}
						<button
							type="button"
							class="settings-action key-bind"
							onclick={() => startRecording(actionId)}
							title={$t('keyboard.clickToRebind')}
						>
							<KeyPill text={formatChord(chord)} />
						</button>
					{:else}
						<button type="button" class="settings-action" onclick={() => startRecording(actionId)}
							>{$t('keyboard.unassigned')}</button
						>
					{/if}

					{#if conflict}
						<span
							class="text-[0.5625rem] text-amber-500 ml-1"
							title={$t('keyboard.conflict', { action: ACTION_LABELS[conflict] })}>!</span
						>
					{/if}
				</div>
			</div>
		{/each}
	</div>

	<p class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-3">
		{$t('keyboard.rebindHint')}
	</p>
</div>

<style>
	@reference "../../../app.css";

	.shortcut-row {
		@apply flex items-center gap-2 px-1 py-1.5;
		border-bottom: 1px solid var(--app-divider);
	}

	.shortcut-row:last-child {
		border-bottom: none;
	}

	.key-bind :global(.key-pill) {
		height: auto;
		padding: 0;
		background: transparent;
		color: inherit;
	}

	.recording-indicator {
		display: inline-flex;
		align-items: center;
		min-height: 1.75rem;
		padding: 0 0.875rem;
		border-radius: 0.75rem;
		font-size: 0.75rem;
		font-weight: 600;
		background: var(--app-bg);
		color: var(--app-fg);
		box-shadow: 0 1px 3px color-mix(in oklab, black 10%, transparent);
		animation: pulse-subtle 1.5s ease-in-out infinite;
	}

	@keyframes pulse-subtle {
		0%,
		100% {
			opacity: 1;
		}
		50% {
			opacity: 0.6;
		}
	}

	.kbd-inline {
		@apply inline px-0.5 py-px rounded text-[0.5625rem] font-mono bg-gray-100 dark:bg-white/6 text-gray-500;
	}
</style>
