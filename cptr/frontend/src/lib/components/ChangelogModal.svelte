<script lang="ts">
	import { fetchJSON } from '$lib/apis';
	import Modal from './Modal.svelte';
	import Icon from './Icon.svelte';
	import { savePreferences } from '$lib/apis/state';
	import { appVersion, lastSeenVersion, showChangelog } from '$lib/stores';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ChangelogNotes from './ChangelogNotes.svelte';
	import type { ChangelogVersion } from '$lib/apis/update';
	import { t } from '$lib/i18n';

	let changelog = $state<Record<string, ChangelogVersion> | null>(null);
	let error = $state(false);

	const versions = $derived.by(() => (changelog ? Object.entries(changelog) : []));

	$effect(() => {
		if ($showChangelog && !changelog && !error) {
			fetchJSON<Record<string, ChangelogVersion>>('/api/changelog')
				.then((data) => {
					changelog = data;
				})
				.catch(() => {
					error = true;
				});
		}
	});

	function handleClose() {
		if ($appVersion) {
			lastSeenVersion.set($appVersion);
			// Persist immediately so Done and the close button survive a quick reload.
			savePreferences({ version: $appVersion }).catch(() => {});
		}
		showChangelog.set(false);
	}
</script>

{#if $showChangelog}
	<Modal onclose={handleClose} class="w-full max-w-2xl mx-4 md:mx-0 flex flex-col max-h-[72vh]">
		<div class="flex items-center gap-3 px-4 pt-4 pb-2 shrink-0">
			<div class="min-w-0 flex-1">
				<h2 class="text-sm font-medium text-gray-900 dark:text-white">
					{$t('changelog.whatsNew')}
				</h2>
				<p class="mt-0.5 text-[0.6875rem] text-gray-400 dark:text-gray-500">
					{$t('changelog.subtitle')}{#if $appVersion}&nbsp;· v{$appVersion}{/if}
				</p>
			</div>

			<button
				class="flex h-7 w-7 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-600 dark:hover:bg-white/6 dark:hover:text-gray-300 transition-colors duration-75"
				onclick={handleClose}
				aria-label={$t('common.close')}
			>
				<Icon name="xmark" size={14} />
			</button>
		</div>

		<div class="flex-1 min-h-0 overflow-y-auto px-4 py-3">
			{#if versions.length > 0}
				<ChangelogNotes {versions} />
			{:else if error}
				<div class="flex flex-col items-center justify-center py-16 gap-2 text-center">
					<p class="text-xs text-gray-500 dark:text-gray-400">{$t('changelog.loadError')}</p>
					<button
						class="rounded-lg bg-gray-100 px-3 py-1.5 text-xs font-medium text-gray-600 hover:text-gray-900 dark:bg-white/6 dark:text-gray-400 dark:hover:text-white transition-colors duration-75"
						onclick={() => (error = false)}
					>
						{$t('changelog.retry')}
					</button>
				</div>
			{:else}
				<div class="flex flex-col items-center justify-center py-16 gap-3">
					<Spinner size={16} />
					<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600"
						>{$t('changelog.loading')}</span
					>
				</div>
			{/if}
		</div>

		<div class="flex items-center justify-end px-4 pt-1 pb-4 shrink-0">
			<button
				class="text-[0.8125rem] text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors duration-100"
				onclick={handleClose}
			>
				{$t('changelog.done')}
			</button>
		</div>
	</Modal>
{/if}
