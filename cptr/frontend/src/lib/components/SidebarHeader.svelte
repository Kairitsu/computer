<script lang="ts">
	import { goto } from '$app/navigation';
	import { requestHomeChat, sidebarOpen } from '$lib/stores';
	import { t } from '$lib/i18n';
	import { tooltip } from '$lib/tooltip';
	import Icon from './Icon.svelte';
	import GrokMark from './brand/GrokMark.svelte';

	function goHome(e: MouseEvent) {
		e.preventDefault();
		requestHomeChat();
		goto('/');
		if (typeof window !== 'undefined' && window.innerWidth < 768) sidebarOpen.set(false);
	}
</script>

<div
	data-intro="sidebar-header"
	class="flex items-center justify-between h-9 pl-3.5 pr-1.5 shrink-0 border-b border-gray-200 dark:border-white/6"
	style="border-color: var(--app-border);"
>
	<a
		href="/"
		class="brand flex items-center gap-2 text-[0.8125rem] font-semibold tracking-tight text-gray-900 dark:text-white"
		onclick={goHome}><GrokMark size={16} title="Grok" />Grok</a
	>
	<button
		class="flex items-center justify-center w-7 h-7 rounded-lg text-gray-300 hover:text-gray-500 dark:text-gray-600 dark:hover:text-gray-400 transition-colors duration-100"
		onclick={() => sidebarOpen.set(false)}
		aria-label={$t('sidebar.collapse')}
		use:tooltip={$t('sidebar.collapse')}
	>
		<Icon name="sidebar-expand" size={14} />
	</button>
</div>

<style>
	.brand :global(.grok-mark) {
		transition: transform 0.45s cubic-bezier(0.22, 1, 0.36, 1);
	}

	.brand:hover :global(.grok-mark) {
		transform: rotate(-12deg) scale(1.06);
	}
</style>
