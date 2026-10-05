<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { get } from 'svelte/store';
	import Icon from './Icon.svelte';
	import Modal from './Modal.svelte';
	import General from './Settings/General.svelte';
	import Appearance from './Settings/Appearance.svelte';
	import Usage from './Settings/Usage.svelte';
	import PWA from './Settings/PWA.svelte';
	import Account from './Settings/Account.svelte';
	import Keyboard from './Settings/Keyboard.svelte';
	import Agents from './Admin/Agents.svelte';
	import { session } from '$lib/session';
	import { t } from '$lib/i18n';

	type Tab = 'general' | 'appearance' | 'usage' | 'agents' | 'keyboard' | 'account' | 'pwa';

	interface Props {
		onclose: () => void;
		initialTab?: string;
	}

	let { onclose, initialTab = 'general' }: Props = $props();

	// Pages that were merged into another keep opening where their settings now live.
	const mergedTabs: Record<string, Tab> = {
		notifications: 'general',
		workspace: 'general',
		models: 'agents'
	};

	function normalizeTab(tab: string): Tab {
		const validTabs: Tab[] = ['general', 'appearance', 'usage', 'agents', 'keyboard', 'account'];
		if (tab in mergedTabs) return mergedTabs[tab];
		return validTabs.includes(tab as Tab) ? (tab as Tab) : 'general';
	}

	let activeTab = $state<Tab>(untrack(() => normalizeTab(initialTab)));
	let showPwaSettings = $state(false);

	const isAdmin = $derived($session?.role === 'admin');
	const tr = (key: string) => (get(t) as (key: string) => string)(key);

	type SettingsTab = { id: Tab; label: string; icon: string };

	const tabs: SettingsTab[] = $derived.by(() => {
		const tabs: SettingsTab[] = [
			{ id: 'general', label: tr('settings.general'), icon: 'settings' },
			{ id: 'appearance', label: tr('settings.appearance'), icon: 'sun-light' },
			{ id: 'usage', label: tr('usage.title'), icon: 'usage' }
		];
		if (isAdmin) tabs.push({ id: 'agents', label: tr('admin.agents'), icon: 'terminal' });
		tabs.push(
			{ id: 'keyboard', label: tr('settings.keyboard'), icon: 'keyboard' },
			{ id: 'account', label: tr('settings.account'), icon: 'user' }
		);
		if (showPwaSettings) tabs.push({ id: 'pwa', label: 'PWA', icon: 'phone' });
		return tabs;
	});

	onMount(() => {
		showPwaSettings = isInstalledPwa();
		if (showPwaSettings && initialTab === 'pwa') {
			activeTab = 'pwa';
		} else if (!$session || ($session.role !== 'admin' && normalizeTab(initialTab) === 'agents')) {
			activeTab = 'general';
		} else {
			activeTab = normalizeTab(initialTab);
		}
	});

	function isInstalledPwa(): boolean {
		const nav = navigator as Navigator & { standalone?: boolean };
		return (
			window.matchMedia('(display-mode: standalone)').matches ||
			window.matchMedia('(display-mode: window-controls-overlay)').matches ||
			nav.standalone === true
		);
	}
</script>

<Modal
	{onclose}
	class="w-full max-w-3xl lg:max-w-4xl xl:max-w-5xl mx-4 md:mx-0 flex flex-col md:flex-row max-h-[85vh] lg:max-h-[90vh] md:h-[35rem] lg:h-[42rem] xl:h-[46rem]"
>
	<nav
		class="shrink-0 min-w-0 md:min-h-0 overflow-x-auto md:overflow-x-hidden md:overflow-y-auto scrollbar-none border-b md:border-b-0 md:border-r border-gray-200 dark:border-white/6 md:w-[11.25rem]"
	>
		<div class="flex w-max min-w-full md:w-auto md:min-w-0 md:flex-col p-1 gap-px">
			<button
				class="flex items-center gap-1.5 h-7 px-2 md:w-full shrink-0 rounded-lg text-xs text-gray-400 dark:text-gray-600 hover:text-gray-700 dark:hover:text-gray-300 transition-colors duration-75 md:mb-1"
				onclick={onclose}
			>
				<Icon name="chevron-left" size={12} />
				<span>{tr('settings.back')}</span>
			</button>

			{#each tabs as tab}
				<button
					class="flex items-center gap-1.5 h-7 px-2 md:w-full shrink-0 rounded-lg text-xs text-left transition-colors duration-75
						{activeTab === tab.id
						? 'font-medium text-gray-900 dark:text-white bg-gray-100 dark:bg-white/6'
						: 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'}"
					onclick={() => (activeTab = tab.id)}
				>
					<Icon name={tab.icon} size={14} />
					{tab.label}
				</button>
			{/each}
		</div>
	</nav>

	<div class="flex-1 overflow-y-auto scrollbar-none min-h-0 p-4 md:px-5">
		{#if activeTab === 'general'}
			<General {showPwaSettings} />
		{:else if activeTab === 'appearance'}
			<Appearance />
		{:else if activeTab === 'usage'}
			<Usage />
		{:else if activeTab === 'agents'}
			<Agents />
		{:else if activeTab === 'keyboard'}
			<Keyboard />
		{:else if activeTab === 'account'}
			<Account />
		{:else if activeTab === 'pwa' && showPwaSettings}
			<PWA />
		{/if}
	</div>
</Modal>
