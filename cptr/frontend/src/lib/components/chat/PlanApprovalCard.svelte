<script lang="ts">
	import { t } from '$lib/i18n';

	interface Props {
		onapprove: () => void;
		onrevise: () => void;
	}

	let { onapprove, onrevise }: Props = $props();
</script>

<section
	class="my-1 flex flex-wrap items-center justify-between gap-x-3 gap-y-2 rounded-2xl bg-gray-100/70 px-3.5 py-2.5 dark:bg-white/[0.055]"
>
	<div class="min-w-0">
		<div class="text-[0.6875rem] font-medium tracking-wide text-gray-500 dark:text-gray-400">
			{$t('chat.planReady')}
		</div>
		<div class="mt-0.5 text-xs leading-relaxed text-gray-600 dark:text-gray-300">
			{$t('chat.planReadyHint')}
		</div>
	</div>
	<div class="flex shrink-0 items-center gap-1.5">
		<button
			type="button"
			class="rounded-lg px-2.5 py-1.5 text-xs text-gray-500 transition-colors hover:bg-white/70 hover:text-gray-800 dark:text-gray-400 dark:hover:bg-white/10 dark:hover:text-gray-100"
			onclick={onrevise}
		>
			{$t('chat.planRevise')}
		</button>
		<button
			type="button"
			class="rounded-lg bg-gray-900 px-3 py-1.5 text-xs font-medium text-white transition active:scale-[0.98] hover:bg-gray-800 dark:bg-white dark:text-black dark:hover:bg-white/90"
			onclick={onapprove}
		>
			{$t('chat.planApprove')}
		</button>
	</div>
</section>
