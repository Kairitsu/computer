<script lang="ts">
	/**
	 * Settings → Agents → Update. One dialog at a time, in the same shell as
	 * the sidebar update check: version, the publisher's notes, then confirm.
	 */
	import Modal from '$lib/components/Modal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import MarkdownRenderer from '$lib/components/markdown/MarkdownRenderer.svelte';
	import { t } from '$lib/i18n';
	import type { AgentCliUpdate, AgentCliUpdateReport } from '$lib/apis/admin';

	type Phase = 'checking' | 'summary' | 'ask' | 'installing' | 'failed' | 'done';

	interface Props {
		phase: Phase;
		report: AgentCliUpdateReport | null;
		current: AgentCliUpdate | null;
		installedVersion: string | null;
		errorCode: string | null;
		errorDetail: string;
		hasNext: boolean;
		onclose: () => void;
		onlater: () => void;
		onconfirm: () => void;
		onretry: () => void;
		onadvance: () => void;
	}

	let {
		phase,
		report,
		current,
		installedVersion,
		errorCode,
		errorDetail,
		hasNext,
		onclose,
		onlater,
		onconfirm,
		onretry,
		onadvance
	}: Props = $props();

	const busy = $derived(phase === 'checking' || phase === 'installing');

	function problem(code: string | null): string {
		const key = `agentUpdate.error.${code || 'check_failed'}`;
		const text = $t(key);
		return text === key ? $t('agentUpdate.error.check_failed') : text;
	}

	function versionText(entry: AgentCliUpdate): string {
		if (entry.current_version && entry.latest_version && entry.current_version !== entry.latest_version) {
			return `${entry.current_version} → ${entry.latest_version}`;
		}
		return entry.current_version || entry.latest_version || '';
	}
</script>

<Modal onclose={busy ? () => {} : onclose} class="w-full max-w-lg mx-4 md:mx-0 flex flex-col max-h-[80vh]">
	<header class="flex items-start gap-3 px-5 pt-5 pb-3 shrink-0">
		<div class="min-w-0 flex-1">
			<h2 class="text-sm font-semibold text-gray-900 dark:text-white">
				{#if phase === 'summary'}
					{$t('agentUpdate.upToDateTitle')}
				{:else if phase === 'failed'}
					{$t('agentUpdate.failedTitle')}
				{:else if phase === 'installing'}
					{$t('agentUpdate.installing')}
				{:else if phase === 'done'}
					{$t('agentUpdate.installedTitle')}
				{:else if phase === 'ask' && current?.update_available}
					{$t('agentUpdate.available', { name: current.name })}
				{:else if phase === 'ask' && current}
					{current.name}
				{:else}
					{$t('agentUpdate.checking')}
				{/if}
			</h2>
			{#if phase === 'ask' && current}
				<p class="mt-0.5 text-[0.6875rem] text-gray-400 dark:text-gray-500 tabular-nums">
					{versionText(current)}
					{#if current.channel}
						<span class="mx-1.5">·</span>{current.channel}
					{/if}
				</p>
			{:else if phase === 'done' && installedVersion}
				<p class="mt-0.5 text-[0.6875rem] text-gray-400 dark:text-gray-500 tabular-nums">
					{installedVersion}
				</p>
			{:else if phase === 'checking'}
				<p class="mt-0.5 text-[0.6875rem] text-gray-400 dark:text-gray-500">
					{$t('agentUpdate.readingNotes')}
				</p>
			{/if}
		</div>
		{#if !busy}
			<button
				class="-mr-1.5 -mt-1 flex h-7 w-7 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-600 dark:hover:bg-white/6 dark:hover:text-white transition-colors duration-75"
				onclick={onclose}
				aria-label={$t('common.close')}
			>
				<Icon name="xmark" size={14} />
			</button>
		{/if}
	</header>

	<div class="flex-1 min-h-0 overflow-y-auto px-5 pb-2">
		{#if phase === 'checking' || phase === 'installing'}
			<div class="flex flex-col items-center justify-center gap-3 py-14">
				<Spinner size={18} />
				<span class="text-xs text-gray-500 dark:text-gray-400">
					{phase === 'installing' ? $t('agentUpdate.installing') : $t('agentUpdate.checking')}
				</span>
			</div>
		{:else if phase === 'summary'}
			<div class="flex flex-col items-center gap-3 py-8 text-center">
				<span
					class="flex h-10 w-10 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
				>
					<Icon name="check" size={18} />
				</span>
				<ul class="w-full space-y-1.5 text-left">
					{#each report?.agents ?? [] as entry (entry.agent)}
						<li
							class="flex items-center justify-between gap-3 rounded-xl px-3 py-2 text-xs"
							style="background: color-mix(in srgb, var(--app-fg) 6%, transparent);"
						>
							<span class="text-gray-700 dark:text-gray-300">{entry.name}</span>
							<span class="tabular-nums text-gray-400 dark:text-gray-500"
								>{entry.current_version || '—'}</span
							>
						</li>
					{/each}
				</ul>
			</div>
		{:else if phase === 'done'}
			<div class="flex flex-col items-center gap-2 py-10 text-center">
				<span
					class="flex h-10 w-10 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
				>
					<Icon name="check" size={18} />
				</span>
				<p class="text-xs text-gray-600 dark:text-gray-300">
					{$t('agentUpdate.installed', { version: installedVersion || '' })}
				</p>
			</div>
		{:else if phase === 'failed'}
			<div class="py-2">
				<p class="text-xs leading-relaxed text-gray-600 dark:text-gray-300">{problem(errorCode)}</p>
				{#if errorDetail}
					<pre
						class="mt-3 max-h-40 overflow-auto whitespace-pre-wrap rounded-xl bg-gray-50 p-3 font-mono text-[0.625rem] leading-relaxed text-gray-600 dark:bg-white/4 dark:text-gray-400">{errorDetail}</pre>
				{/if}
			</div>
		{:else if phase === 'ask' && current}
			{#if current.error && !current.update_available}
				<p class="text-xs leading-relaxed text-gray-600 dark:text-gray-300">
					{problem(current.error)}
				</p>
				{#if current.detail}
					<p class="mt-2 text-[0.6875rem] leading-relaxed text-gray-400 dark:text-gray-500">
						{current.detail}
					</p>
				{/if}
			{:else}
				{#if current.notes_available}
					<div class="py-1 text-xs leading-relaxed text-gray-700 dark:text-gray-300">
						<MarkdownRenderer content={current.notes} />
					</div>
				{:else}
					<p class="text-xs leading-relaxed text-gray-600 dark:text-gray-300">
						{$t('agentUpdate.noNotes')}
					</p>
				{/if}
				<p
					class="mt-4 rounded-xl bg-amber-500/10 px-3 py-2.5 text-xs leading-relaxed text-amber-800 dark:text-amber-300"
				>
					{$t('agentUpdate.running')}
				</p>
			{/if}
		{/if}
	</div>

	<footer class="flex flex-wrap items-center justify-end gap-2 px-5 pt-3 pb-5 shrink-0">
		{#if phase === 'ask' && current?.update_available && current.can_update}
			<button
				class="app-muted app-interactive h-8 rounded-lg px-3 text-xs font-medium transition-colors duration-75"
				onclick={onlater}>{$t('agentUpdate.later')}</button
			>
			<button
				class="h-8 rounded-lg bg-gray-900 px-3.5 text-xs font-medium text-white transition-colors duration-75 hover:bg-gray-800 dark:bg-white dark:text-black dark:hover:bg-white/90"
				onclick={onconfirm}>{$t('agentUpdate.updateNow')}</button
			>
		{:else if phase === 'failed' && current}
			<button
				class="app-muted app-interactive h-8 rounded-lg px-3 text-xs font-medium transition-colors duration-75"
				onclick={onclose}>{$t('agentUpdate.close')}</button
			>
			<button
				class="h-8 rounded-lg bg-gray-900 px-3.5 text-xs font-medium text-white transition-colors duration-75 hover:bg-gray-800 dark:bg-white dark:text-black dark:hover:bg-white/90"
				onclick={onretry}>{$t('agentUpdate.retry')}</button
			>
		{:else if phase === 'done' || phase === 'ask'}
			<button
				class="h-8 rounded-lg bg-gray-900 px-3.5 text-xs font-medium text-white transition-colors duration-75 hover:bg-gray-800 dark:bg-white dark:text-black dark:hover:bg-white/90"
				onclick={phase === 'done' ? onadvance : onclose}
				>{hasNext ? $t('agentUpdate.continue') : $t('agentUpdate.done')}</button
			>
		{:else if phase === 'summary' || (phase === 'failed' && !current)}
			<button
				class="h-8 rounded-lg bg-gray-900 px-3.5 text-xs font-medium text-white transition-colors duration-75 hover:bg-gray-800 dark:bg-white dark:text-black dark:hover:bg-white/90"
				onclick={onclose}>{$t('agentUpdate.done')}</button
			>
		{/if}
	</footer>
</Modal>
