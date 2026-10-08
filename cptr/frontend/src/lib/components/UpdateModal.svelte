<script lang="ts">
	/**
	 * Sidebar → 检查更新. Compares the install's git checkout with GitHub, shows what
	 * changed, and on confirmation lets the server pull, rebuild and restart itself;
	 * the page reloads once the restarted server answers.
	 */
	import { onDestroy } from 'svelte';
	import Modal from './Modal.svelte';
	import Icon from './Icon.svelte';
	import Spinner from './common/Spinner.svelte';
	import ChangelogNotes from './ChangelogNotes.svelte';
	import { t, locale } from '$lib/i18n';
	import { ApiError } from '$lib/apis';
	import {
		checkForUpdate,
		getServerStartedAt,
		getUpdateStatus,
		startUpdate,
		type UpdateCheck,
		type UpdateStatus
	} from '$lib/apis/update';
	import { showUpdateModal, updateCheck } from '$lib/stores';
	import { requestConfirm } from '$lib/stores/confirm';
	import { INSTALL_COMMAND } from '$lib/constants';

	type Phase = 'checking' | 'result' | 'error' | 'updating' | 'restarting' | 'reloading';

	const COMMITS_PREVIEW = 5;
	const RESTART_PATIENCE_MS = 3 * 60 * 1000;

	let phase = $state<Phase>('checking');
	let info = $state<UpdateCheck | null>(null);
	let progress = $state<UpdateStatus | null>(null);
	let loadError = $state('');
	let showLog = $state(false);
	let showAllCommits = $state(false);
	let copied = $state(false);
	let restartSlow = $state(false);
	let startedAt: number | null = null;
	let restartBegan = 0;
	let pollTimer: ReturnType<typeof setTimeout> | null = null;
	let logEl: HTMLPreElement | undefined = $state();

	const busy = $derived(phase === 'updating' || phase === 'restarting' || phase === 'reloading');
	const failed = $derived(progress?.status === 'failed');
	const finishedWithoutRestart = $derived(progress?.status === 'done');
	const noteVersions = $derived(info ? Object.entries(info.notes) : []);
	const versionChanged = $derived(!!info?.latest && info.latest.version !== info.current.version);
	const blocked = $derived(info?.reason === 'dirty' || info?.reason === 'diverged');
	const visibleCommits = $derived(
		info ? (showAllCommits ? info.commits : info.commits.slice(0, COMMITS_PREVIEW)) : []
	);

	let wasOpen = false;
	$effect(() => {
		const open = $showUpdateModal;
		if (open && !wasOpen) void openDialog();
		if (!open && wasOpen) stopPolling();
		wasOpen = open;
	});

	$effect(() => {
		if (showLog && logEl && progress?.log.length) logEl.scrollTop = logEl.scrollHeight;
	});

	onDestroy(stopPolling);

	async function openDialog() {
		progress = null;
		showLog = false;
		showAllCommits = false;
		restartSlow = false;
		// An update may already be running, e.g. after reloading the page mid-update.
		try {
			const current = await getUpdateStatus();
			if (current.status === 'running' || current.status === 'restarting') {
				progress = current;
				startedAt = await getServerStartedAt();
				phase = current.status === 'running' ? 'updating' : 'restarting';
				restartBegan = Date.now();
				schedulePoll();
				return;
			}
		} catch {
			// Fall through to a normal check.
		}
		await runCheck();
	}

	async function runCheck() {
		phase = 'checking';
		loadError = '';
		try {
			info = await checkForUpdate(true);
			updateCheck.set(info);
			phase = 'result';
		} catch (error) {
			loadError = error instanceof ApiError ? error.message : $t('updater.reason.fetch_failed');
			phase = 'error';
		}
	}

	async function beginUpdate() {
		if (!info) return;
		if (info.active_chats > 0) {
			const ok = await requestConfirm({
				title: $t('updater.activeChatsConfirmTitle'),
				message: $t('updater.activeChats', { count: info.active_chats }),
				confirmLabel: $t('updater.updateNow'),
				cancelLabel: $t('updater.later')
			});
			if (!ok) return;
		}
		startedAt = await getServerStartedAt();
		phase = 'updating';
		try {
			progress = await startUpdate();
			schedulePoll();
		} catch (error) {
			const code = error instanceof ApiError ? error.message : 'unknown';
			progress = { status: 'failed', error: code, detail: '', log: [] };
		}
	}

	function schedulePoll() {
		stopPolling();
		pollTimer = setTimeout(poll, 1000);
	}

	function stopPolling() {
		if (pollTimer) clearTimeout(pollTimer);
		pollTimer = null;
	}

	async function poll() {
		if (phase === 'updating') {
			try {
				progress = await getUpdateStatus();
			} catch {
				// The server is going down to restart.
			}
			if (progress?.status === 'failed' || progress?.status === 'done') return;
			if (progress?.status === 'restarting' || progress?.status === 'idle') {
				phase = 'restarting';
				restartBegan = Date.now();
			}
		}
		if (phase === 'restarting') {
			const now = await getServerStartedAt();
			if (now !== null && startedAt !== null && now !== startedAt) {
				phase = 'reloading';
				setTimeout(() => location.reload(), 700);
				return;
			}
			restartSlow = Date.now() - restartBegan > RESTART_PATIENCE_MS;
		}
		schedulePoll();
	}

	function close() {
		if (busy && !failed && !finishedWithoutRestart) return;
		stopPolling();
		showUpdateModal.set(false);
	}

	async function copyInstallCommand() {
		try {
			await navigator.clipboard.writeText(INSTALL_COMMAND);
			copied = true;
			setTimeout(() => (copied = false), 1500);
		} catch {
			// Clipboard needs a secure context; the command stays selectable.
		}
	}

	function shortSha(sha: string | null | undefined): string {
		return sha ? sha.slice(0, 7) : '';
	}

	function formatDate(iso: string | null | undefined): string {
		if (!iso) return '';
		const date = new Date(iso);
		if (Number.isNaN(date.getTime())) return '';
		try {
			return new Intl.DateTimeFormat($locale, { dateStyle: 'medium' }).format(date);
		} catch {
			return date.toLocaleDateString();
		}
	}

	function stepLabel(key: string): string {
		return $t(`updater.step.${key}`);
	}

	function errorMessage(code: string | null | undefined): string {
		for (const key of [`updater.error.${code}`, `updater.reason.${code}`]) {
			const text = $t(key);
			if (code && text !== key) return text;
		}
		return $t('updater.error.unknown');
	}

	function reasonMessage(code: string | null | undefined): string {
		const key = `updater.reason.${code}`;
		const text = $t(key);
		return text === key ? $t('updater.reason.git_failed') : text;
	}
</script>

{#if $showUpdateModal}
	<Modal onclose={close} class="w-full max-w-lg mx-4 md:mx-0 flex flex-col max-h-[80vh]">
		<header class="flex items-start gap-3 px-5 pt-5 pb-3 shrink-0">
			<div class="min-w-0 flex-1">
				<h2 class="text-sm font-semibold text-gray-900 dark:text-white">
					{#if busy && failed}
						{$t('updater.failedTitle')}
					{:else if busy && finishedWithoutRestart}
						{$t('updater.manualTitle')}
					{:else if busy}
						{$t('updater.updatingTitle')}
					{:else if phase === 'result' && info?.available}
						{$t('updater.available')}
					{:else}
						{$t('updater.title')}
					{/if}
				</h2>
				<p class="mt-0.5 text-[0.6875rem] text-gray-400 dark:text-gray-500 tabular-nums">
					{#if info?.available && info.latest && !busy}
						v{info.current.version} → v{info.latest.version}<span class="mx-1.5">·</span>{$t(
							'updater.newCommits',
							{ count: info.behind }
						)}
					{:else if info}
						{$t('updater.current', { version: info.current.version })}{#if info.current.commit}<span
								class="mx-1.5">·</span
							><span class="font-mono">{shortSha(info.current.commit)}</span>{/if}
					{:else}
						{$t('updater.checking')}
					{/if}
				</p>
			</div>
			{#if !busy || failed || finishedWithoutRestart}
				<button
					class="-mr-1.5 -mt-1 flex h-7 w-7 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:text-gray-600 dark:hover:bg-white/6 dark:hover:text-gray-300 transition-colors duration-75"
					onclick={close}
					aria-label={$t('common.close')}
				>
					<Icon name="xmark" size={14} />
				</button>
			{/if}
		</header>

		<div class="flex-1 min-h-0 overflow-y-auto px-5 pb-2">
			{#if phase === 'checking'}
				<div class="flex flex-col items-center justify-center gap-3 py-14">
					<Spinner size={18} />
					<span class="text-xs text-gray-500 dark:text-gray-400">{$t('updater.checking')}</span>
				</div>
			{:else if phase === 'error'}
				<div class="flex flex-col items-center justify-center gap-2 py-12 text-center">
					<p class="text-xs text-gray-600 dark:text-gray-300">{$t('updater.errorTitle')}</p>
					<p class="max-w-sm text-[0.6875rem] text-gray-400 dark:text-gray-500">{loadError}</p>
				</div>
			{:else if phase === 'result' && info}
				{#if info.reason === 'not_git'}
					<div class="py-2">
						<p class="text-xs leading-relaxed text-gray-600 dark:text-gray-300">
							{$t('updater.reason.not_git')}
						</p>
						<div
							class="mt-3 flex items-center gap-2 rounded-xl border px-3 py-2"
							style="border-color: var(--app-border);"
						>
							<code
								class="min-w-0 flex-1 overflow-x-auto whitespace-nowrap font-mono text-[0.6875rem] text-gray-700 dark:text-gray-300 select-all"
								>{INSTALL_COMMAND}</code
							>
							<button
								class="shrink-0 rounded-md px-2 py-1 text-[0.6875rem] font-medium text-gray-500 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-white/6 dark:hover:text-white transition-colors duration-75"
								onclick={copyInstallCommand}
								>{copied ? $t('updater.copied') : $t('updater.copy')}</button
							>
						</div>
					</div>
				{:else if !info.supported || (info.reason && !blocked)}
					<div class="flex flex-col items-center justify-center gap-2 py-12 text-center">
						<p class="text-xs text-gray-600 dark:text-gray-300">{reasonMessage(info.reason)}</p>
						{#if info.detail}
							<p class="max-w-sm break-words text-[0.6875rem] text-gray-400 dark:text-gray-500">
								{info.detail}
							</p>
						{/if}
					</div>
				{:else if !info.available}
					<div class="flex flex-col items-center justify-center gap-2.5 py-12 text-center">
						<span
							class="flex h-10 w-10 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
						>
							<Icon name="check" size={18} />
						</span>
						<p class="text-[0.8125rem] font-medium text-gray-900 dark:text-white">
							{$t('updater.upToDate')}
						</p>
						<p class="text-[0.6875rem] text-gray-400 dark:text-gray-500 tabular-nums">
							v{info.current.version}{#if info.current.date}<span class="mx-1.5">·</span
								>{formatDate(info.current.date)}{/if}
						</p>
					</div>
				{:else}
					{#if noteVersions.length > 0}
						<ChangelogNotes versions={noteVersions} />
					{:else}
						<p class="text-xs leading-relaxed text-gray-500 dark:text-gray-400">
							{versionChanged ? $t('updater.noNotes') : $t('updater.sameVersion')}
						</p>
					{/if}

					{#if info.commits.length > 0}
						<div class="mt-5">
							<div class="mb-1.5 flex items-baseline justify-between gap-2">
								<h3 class="text-[0.6875rem] font-medium text-gray-400 dark:text-gray-500">
									{$t('updater.commitsTitle')}
								</h3>
								{#if info.repo_url && info.current.commit && info.latest?.commit}
									<a
										href="{info.repo_url}/compare/{info.current.commit}...{info.latest.commit}"
										target="_blank"
										rel="noopener noreferrer"
										class="text-[0.6875rem] text-gray-400 hover:text-gray-700 hover:underline dark:text-gray-500 dark:hover:text-gray-300"
										>{$t('updater.viewDiff')}</a
									>
								{/if}
							</div>
							<ul class="divide-y rounded-xl border" style="border-color: var(--app-border);">
								{#each visibleCommits as commit (commit.commit)}
									<li
										class="flex items-baseline gap-2.5 px-3 py-2 text-xs"
										style="border-color: var(--app-border);"
									>
										<span class="min-w-0 flex-1 text-gray-700 dark:text-gray-300"
											>{commit.subject}</span
										>
										{#if info.repo_url}
											<a
												href="{info.repo_url}/commit/{commit.commit}"
												target="_blank"
												rel="noopener noreferrer"
												class="shrink-0 font-mono text-[0.625rem] text-gray-400 hover:text-gray-700 hover:underline dark:text-gray-500 dark:hover:text-gray-300"
												>{shortSha(commit.commit)}</a
											>
										{:else}
											<span
												class="shrink-0 font-mono text-[0.625rem] text-gray-400 dark:text-gray-500"
												>{shortSha(commit.commit)}</span
											>
										{/if}
									</li>
								{/each}
							</ul>
							{#if info.commits.length > COMMITS_PREVIEW}
								<button
									class="mt-1.5 text-[0.6875rem] text-gray-400 hover:text-gray-700 dark:text-gray-500 dark:hover:text-gray-300"
									onclick={() => (showAllCommits = !showAllCommits)}
								>
									{showAllCommits
										? $t('updater.showFewerCommits')
										: $t('updater.showAllCommits', { count: info.commits.length })}
								</button>
							{/if}
						</div>
					{/if}

					{#if blocked}
						<div
							class="mt-4 rounded-xl bg-amber-500/10 px-3 py-2.5 text-xs leading-relaxed text-amber-800 dark:text-amber-300"
						>
							{reasonMessage(info.reason)}
							{#if info.reason === 'dirty' && info.dirty_files.length}
								<ul class="mt-1.5 font-mono text-[0.6875rem]">
									{#each info.dirty_files.slice(0, 8) as file (file)}
										<li class="truncate">{file}</li>
									{/each}
								</ul>
							{/if}
						</div>
					{:else if info.active_chats > 0}
						<p
							class="mt-4 rounded-xl bg-amber-500/10 px-3 py-2.5 text-xs leading-relaxed text-amber-800 dark:text-amber-300"
						>
							{$t('updater.activeChats', { count: info.active_chats })}
						</p>
					{/if}
					{#if !blocked && !info.can_restart}
						<p class="mt-3 text-[0.6875rem] leading-relaxed text-gray-400 dark:text-gray-500">
							{$t('updater.noAutoRestart')}
						</p>
					{/if}
				{/if}
			{:else if busy}
				<ol class="space-y-2 py-1">
					{#each progress?.steps ?? [] as step (step.key)}
						<li class="flex items-center gap-2.5 text-xs">
							<span class="flex h-4 w-4 shrink-0 items-center justify-center">
								{#if step.status === 'running' || (step.key === 'restart' && phase === 'restarting')}
									<Spinner size={12} />
								{:else if step.status === 'done' || (step.key === 'restart' && phase === 'reloading')}
									<span class="text-emerald-600 dark:text-emerald-400"
										><Icon name="check" size={13} /></span
									>
								{:else if step.status === 'failed'}
									<span class="text-red-500"><Icon name="xmark" size={13} /></span>
								{:else if step.status === 'skipped'}
									<span class="text-gray-300 dark:text-gray-600"
										><Icon name="check" size={13} /></span
									>
								{:else}
									<span class="h-1.5 w-1.5 rounded-full bg-gray-300 dark:bg-gray-600"></span>
								{/if}
							</span>
							<span
								class={step.status === 'pending' &&
								!(step.key === 'restart' && phase !== 'updating')
									? 'text-gray-400 dark:text-gray-500'
									: step.status === 'skipped'
										? 'text-gray-400 dark:text-gray-500'
										: 'text-gray-800 dark:text-gray-200'}
								>{stepLabel(step.key)}{#if step.status === 'skipped'}<span class="ml-1"
										>{$t('updater.skipped')}</span
									>{/if}</span
							>
						</li>
					{/each}
				</ol>

				{#if failed}
					<div
						class="mt-4 rounded-xl bg-red-500/10 px-3 py-2.5 text-xs leading-relaxed text-red-700 dark:text-red-300"
					>
						<p>{errorMessage(progress?.error)}</p>
						<p class="mt-1 text-[0.6875rem] opacity-80">
							{progress?.rollback_failed
								? $t('updater.rollbackFailed')
								: progress?.rolled_back
									? $t('updater.rolledBack')
									: $t('updater.stillRunning')}
						</p>
					</div>
				{:else if finishedWithoutRestart}
					<p class="mt-4 text-xs leading-relaxed text-gray-600 dark:text-gray-300">
						{$t('updater.manualHint')}
					</p>
				{:else if phase === 'reloading'}
					<p class="mt-4 text-xs text-gray-600 dark:text-gray-300">{$t('updater.reloading')}</p>
				{:else if restartSlow}
					<p class="mt-4 text-xs leading-relaxed text-amber-700 dark:text-amber-300">
						{$t('updater.restartSlow')}
					</p>
				{:else}
					<p class="mt-4 text-[0.6875rem] leading-relaxed text-gray-400 dark:text-gray-500">
						{phase === 'restarting' ? $t('updater.restarting') : $t('updater.updatingHint')}
					</p>
				{/if}

				{#if progress?.log.length}
					<button
						class="mt-3 text-[0.6875rem] text-gray-400 hover:text-gray-700 dark:text-gray-500 dark:hover:text-gray-300"
						onclick={() => (showLog = !showLog)}
					>
						{showLog ? $t('updater.hideLog') : $t('updater.showLog')}
					</button>
					{#if showLog}
						<pre
							bind:this={logEl}
							class="mt-1.5 max-h-48 overflow-auto rounded-xl bg-gray-50 p-3 font-mono text-[0.625rem] leading-relaxed text-gray-600 dark:bg-white/4 dark:text-gray-400">{progress.log.join(
								'\n'
							)}</pre>
					{/if}
				{/if}
			{/if}
		</div>

		<footer class="flex items-center justify-end gap-2 px-5 pt-3 pb-5 shrink-0">
			{#if phase === 'result' && info?.available && info.supported && !info.reason}
				<button
					class="app-muted app-interactive h-8 rounded-lg px-3 text-xs font-medium transition-colors duration-75"
					onclick={close}>{$t('updater.later')}</button
				>
				<button
					class="h-8 rounded-lg bg-gray-900 px-3.5 text-xs font-medium text-white transition-colors duration-75 hover:bg-gray-800 dark:bg-white dark:text-black dark:hover:bg-white/90"
					onclick={beginUpdate}>{$t('updater.updateNow')}</button
				>
			{:else if phase === 'result' || phase === 'error'}
				{#if info?.reason !== 'not_git'}
					<button
						class="app-muted app-interactive h-8 rounded-lg px-3 text-xs font-medium transition-colors duration-75"
						onclick={runCheck}>{$t('updater.recheck')}</button
					>
				{/if}
				<button
					class="h-8 rounded-lg bg-gray-900 px-3.5 text-xs font-medium text-white transition-colors duration-75 hover:bg-gray-800 dark:bg-white dark:text-black dark:hover:bg-white/90"
					onclick={close}>{$t('changelog.done')}</button
				>
			{:else if failed || finishedWithoutRestart}
				<button
					class="h-8 rounded-lg bg-gray-900 px-3.5 text-xs font-medium text-white transition-colors duration-75 hover:bg-gray-800 dark:bg-white dark:text-black dark:hover:bg-white/90"
					onclick={close}>{$t('common.close')}</button
				>
			{/if}
		</footer>
	</Modal>
{/if}
