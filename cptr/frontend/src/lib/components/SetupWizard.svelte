<script lang="ts">
	import { formatChord, keybindings } from '$lib/stores/keybindings';
	import { t } from '$lib/i18n';

	interface Props {
		oncomplete: () => void;
	}

	let { oncomplete }: Props = $props();

	let step = $state(0);
	const totalSteps = 2;

	function next() {
		if (step < totalSteps - 1) step++;
	}

	function finish() {
		oncomplete();
	}

	let searchShortcut = $derived(formatChord($keybindings.quickOpen));
</script>

<div
	class="app-theme flex items-center justify-center h-dvh bg-white dark:bg-black p-6"
	style="background: var(--app-bg); color: var(--app-fg);"
>
	<div class="w-full max-w-md">
		<!-- Progress dots -->
		<div class="flex gap-1.5 mb-6">
			{#each Array(totalSteps) as _, i}
				<div
					class="h-0.5 flex-1 rounded-full transition-colors duration-200
						{i <= step ? 'bg-gray-900 dark:bg-white' : 'bg-gray-200 dark:bg-white/8'}"
				></div>
			{/each}
		</div>

		{#if step === 0}
			<!-- Welcome -->
			<div class="animate-in">
				<h1 class="text-lg tracking-tight text-gray-900 dark:text-white mb-1">
					{$t('onboarding.welcomeTitle')}
				</h1>
				<p class="text-[0.8125rem] text-gray-500 dark:text-gray-500 mb-6 leading-relaxed">
					{$t('onboarding.welcomeDesc')}
				</p>

				<button
					class="text-[0.8125rem] text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors duration-100"
					onclick={next}
				>
					{$t('onboarding.getStarted')}
				</button>
			</div>
		{:else if step === 1}
			<!-- Ready -->
			<div class="animate-in">
				<h2 class="text-xs text-gray-400 dark:text-gray-600 mb-1">
					{$t('onboarding.ready')}
				</h2>
				<p class="text-[0.8125rem] text-gray-500 dark:text-gray-500 mb-5 leading-relaxed">
					{$t('onboarding.readyDesc')}
				</p>

				<div class="flex flex-col gap-1.5 mb-6">
					<p class="text-[0.75rem] text-gray-500 dark:text-gray-500">
						{$t('onboarding.tipSearch', { shortcut: searchShortcut })}
					</p>
					<p class="text-[0.75rem] text-gray-500 dark:text-gray-500">
						{$t('onboarding.tipTerminal')}
					</p>
					<p class="text-[0.75rem] text-gray-500 dark:text-gray-500">
						{$t('onboarding.tipMobile')}
					</p>
				</div>

				<button
					class="text-[0.8125rem] text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors duration-100"
					onclick={finish}
				>
					{$t('onboarding.startUsing')}
				</button>
			</div>
		{/if}
	</div>
</div>

<style>
	.animate-in {
		animation: fadeIn 0.15s ease-out;
	}

	@keyframes fadeIn {
		from {
			opacity: 0;
			transform: translateY(0.25rem);
		}
		to {
			opacity: 1;
			transform: translateY(0);
		}
	}
</style>
