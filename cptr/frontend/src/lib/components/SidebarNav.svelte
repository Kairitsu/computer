<script lang="ts">
	import { goto } from '$app/navigation';
	import { sidebarOpen, showSearch, requestHomeChat } from '$lib/stores';
	import { chatEnabled } from '$lib/stores/chat';
	import { keybindings, formatChord } from '$lib/stores/keybindings';
	import { t } from '$lib/i18n';
	import Icon from './Icon.svelte';
	import KeyPill from './KeyPill.svelte';

	let searchShortcut = $derived(formatChord($keybindings.quickOpen));

	function newChat() {
		requestHomeChat();
		goto('/');
		if (typeof window !== 'undefined' && window.innerWidth < 768) sidebarOpen.set(false);
	}

	function openAutomations(e: MouseEvent) {
		e.preventDefault();
		goto('/scheduled');
		if (typeof window !== 'undefined' && window.innerWidth < 768) sidebarOpen.set(false);
	}
</script>

{#if $chatEnabled}
	<div class="px-1.5 mt-1 shrink-0">
		<button
			class="nav-item group flex items-center gap-2 w-full h-8 px-2 rounded-lg text-[0.8125rem] font-medium text-gray-700 hover:text-gray-900 dark:text-gray-300 dark:hover:text-white transition-colors duration-100"
			onclick={newChat}
		>
			<Icon name="chat-plus" size={15} />
			<span class="flex-1 text-left overflow-hidden text-ellipsis whitespace-nowrap"
				>{$t('sidebar.newChat')}</span
			>
		</button>
	</div>
{/if}

<div class="px-1.5 shrink-0" class:mt-1={!$chatEnabled}>
	<button
		class="nav-item group flex items-center gap-2 w-full h-8 px-2 rounded-lg text-[0.8125rem] text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white transition-colors duration-100"
		onclick={() => showSearch.set(true)}
	>
		<Icon name="search" size={15} />
		<span class="flex-1 text-left overflow-hidden text-ellipsis whitespace-nowrap"
			>{$t('search.search')}</span
		>
		<KeyPill
			text={searchShortcut}
			class="ml-auto shrink-0 opacity-0 group-hover:opacity-100 transition-opacity duration-100"
		/>
	</button>
</div>

{#if $chatEnabled}
	<div class="px-1.5 shrink-0">
		<a
			href="/scheduled"
			class="nav-item flex items-center gap-2 w-full h-8 px-2 rounded-lg text-[0.8125rem] text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white transition-colors duration-100 no-underline"
			onclick={openAutomations}
		>
			<Icon name="clock" size={15} />
			<span class="flex-1 text-left overflow-hidden text-ellipsis whitespace-nowrap"
				>{$t('automations.title')}</span
			>
		</a>
	</div>
{/if}

<style>
	.nav-item:hover {
		background: var(--app-hover);
	}
</style>
