<script lang="ts">
	import { appVersion, showChangelog, showUpdateModal, updateAvailable } from '$lib/stores';
	import { session, clearSession } from '$lib/session';
	import { t } from '$lib/i18n';
	import { keybindings, formatChord } from '$lib/stores/keybindings';
	import { quotaSummary } from '$lib/stores/quota';
	import { tooltip } from '$lib/tooltip';
	import DropdownMenu from './DropdownMenu.svelte';
	import Icon from './Icon.svelte';

	interface Props {
		onsettings: (tab?: string) => void;
		onsysteminfo: () => void;
	}

	let { onsettings, onsysteminfo }: Props = $props();
	let showMenu = $state(false);
	let menuButtonEl: HTMLButtonElement | undefined = $state();

	function openSettings(tab?: string) {
		showMenu = false;
		onsettings(tab);
	}

	function openSystemInfo() {
		showMenu = false;
		onsysteminfo();
	}

	function openUpdates() {
		showMenu = false;
		showUpdateModal.set(true);
	}
</script>

<div
	data-intro="sidebar-footer"
	class="relative flex items-center gap-0.5 px-1 py-1 shrink-0 border-t"
	style="border-color: var(--app-border);"
>
	<button
		bind:this={menuButtonEl}
		class="flex flex-1 min-w-0 items-center gap-2 h-9 px-2 rounded-lg text-[0.8125rem] font-medium text-gray-600 hover:text-gray-900 dark:text-gray-300 dark:hover:text-white transition-colors duration-100"
		onclick={() => (showMenu = !showMenu)}
	>
		<img
			src={$session?.profile_image_url || '/user.png'}
			alt="Avatar"
			class="w-5 h-5 rounded-full object-cover shrink-0"
		/>
		<span class="truncate"
			>{$session?.display_name || $session?.username || $t('sidebar.settings')}</span
		>
		{#if $quotaSummary.source !== 'none'}
			<span
				class="ml-auto shrink-0 text-xs font-semibold tabular-nums"
				use:tooltip={$t('usageIndicator.remainingTooltip')}
				>{Math.round($quotaSummary.remainingPercent)}%</span
			>
		{:else if $appVersion}
			<span
				role="button"
				tabindex="-1"
				onclick={(e) => {
					e.stopPropagation();
					showChangelog.set(true);
				}}
				onkeydown={() => {}}
				class="ml-auto text-[0.625rem] text-gray-400 dark:text-gray-600 hover:text-gray-500 dark:hover:text-gray-400 font-mono hover:underline cursor-pointer"
			>
				v{$appVersion}
			</span>
		{/if}
	</button>
	<button
		class="flex items-center justify-center size-8 shrink-0 rounded-lg text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white transition-colors duration-100"
		onclick={() => openSettings()}
		aria-label={$t('sidebar.settings')}
		use:tooltip={$t('sidebar.settings')}
	>
		<Icon name="settings" size={16} />
	</button>
</div>

{#if showMenu && menuButtonEl}
	<DropdownMenu
		anchor={menuButtonEl}
		matchWidth
		items={[
			...($session
				? [
						{
							label: $session.display_name || $session.username,
							image: $session.profile_image_url || '/user.png',
							onclick: () => openSettings('account')
						}
					]
				: []),
			...($session ? [{ divider: true, label: '', onclick: () => {} }] : []),
			{
				label: $t('sidebar.settings'),
				icon: 'settings',
				shortcut: formatChord($keybindings.openSettings),
				onclick: () => openSettings()
			},
			{
				label: $t('system.infoTitle'),
				icon: 'info',
				onclick: openSystemInfo
			},
			...($session?.role === 'admin'
				? [
						{
							label: $updateAvailable ? $t('sidebar.updateAvailable') : $t('sidebar.checkUpdates'),
							icon: 'refresh',
							onclick: openUpdates
						}
					]
				: []),
			{ divider: true, label: '', onclick: () => {} },
			{ label: $t('sidebar.logOut'), icon: 'log-out', onclick: clearSession }
		]}
		onclose={() => (showMenu = false)}
	/>
{/if}
