<script lang="ts">
	import {
		appVersion,
		showChangelog,
		streamingBehavior,
		showUpdateToastPref,
		updateAvailable,
		updateCheck,
		showUpdateModal
	} from '$lib/stores';
	import type { StreamingBehavior } from '$lib/stores';
	import { t, locale, changeLocale, supportedLocales } from '$lib/i18n';
	import { session } from '$lib/session';
	import { toast } from 'svelte-sonner';
	import { notificationsEnabled, notificationSound } from '$lib/stores/chat';
	import {
		getAdminConfig,
		getChatRetention,
		previewChatRetention,
		updateChatRetention,
		updateConfig,
		type ChatRetention
	} from '$lib/apis/admin';
	import { requestConfirm } from '$lib/stores/confirm';
	import {
		getGrokProcessSettings,
		updateGrokProcessSettings,
		type GrokProcessSettings
	} from '$lib/apis/grok';
	import ToggleSwitch from '../common/ToggleSwitch.svelte';

	interface Props {
		showPwaSettings?: boolean;
	}

	let { showPwaSettings = false }: Props = $props();

	const REPO_URL = 'https://github.com/open-webui/computer';
	const SHARE_TEXT = 'Check out Computer. Your computer, from anywhere.';

	const shareLinks = [
		{
			label: 'X',
			href: `https://x.com/intent/tweet?text=${encodeURIComponent(SHARE_TEXT)}&url=${encodeURIComponent(REPO_URL)}`
		},
		{
			label: 'Reddit',
			href: `https://reddit.com/submit?url=${encodeURIComponent(REPO_URL)}&title=${encodeURIComponent(SHARE_TEXT)}`
		},
		{
			label: 'LinkedIn',
			href: `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(REPO_URL)}`
		}
	];

	let copied = $state(false);
	let resetting = $state(false);
	// Server-wide limits for the Grok processes chats keep between turns; admins set them.
	let processSettings = $state<GrokProcessSettings | null>(null);

	// How long chats are kept, server-wide; admins set it. Idle chats past it are deleted.
	let retention = $state<ChatRetention | null>(null);

	$effect(() => {
		if ($session?.role !== 'admin' || retention) return;
		getChatRetention()
			.then((value) => (retention = value))
			.catch(() => {});
	});

	async function saveRetention(input: HTMLInputElement) {
		if (!retention) return;
		const days = Number(input.value);
		if (
			input.value.trim() &&
			Number.isInteger(days) &&
			days >= 0 &&
			days <= retention.max_days &&
			days !== retention.days
		) {
			try {
				// Saving deletes the chats now out of range, so say how many first.
				const { count } = days > 0 ? await previewChatRetention(days) : { count: 0 };
				const confirmed =
					count === 0 ||
					(await requestConfirm({
						title: $t('general.chatRetentionConfirmTitle'),
						message: $t('general.chatRetentionConfirm', { count, days }),
						confirmLabel: $t('general.chatRetentionConfirmDelete'),
						cancelLabel: $t('common.cancel')
					}));
				if (confirmed) {
					retention = await updateChatRetention(days);
					toast.success($t('settings.saved'));
				}
			} catch {
				toast.error($t('admin.failedToSave'));
			}
		}
		input.value = String(retention.days);
	}

	// Workspace setting, server-wide; admins set it.
	let autoGitignoreDotCptr = $state<boolean | null>(null);
	let savingWorkspace = $state(false);

	$effect(() => {
		if ($session?.role !== 'admin' || processSettings) return;
		getGrokProcessSettings()
			.then((settings) => (processSettings = settings))
			.catch(() => {});
	});

	$effect(() => {
		if ($session?.role !== 'admin' || autoGitignoreDotCptr !== null) return;
		getAdminConfig()
			.then(
				(config) => (autoGitignoreDotCptr = config['workspace.auto_gitignore_dot_cptr'] !== false)
			)
			.catch(() => toast.error($t('admin.failedToLoadConfig')));
	});

	async function saveAutoGitignore(value: boolean) {
		const previous = autoGitignoreDotCptr;
		autoGitignoreDotCptr = value;
		savingWorkspace = true;
		try {
			await updateConfig({ 'workspace.auto_gitignore_dot_cptr': value });
			toast.success($t('settings.saved'));
		} catch {
			autoGitignoreDotCptr = previous;
			toast.error($t('admin.failedToSave'));
		} finally {
			savingWorkspace = false;
		}
	}

	async function toggleNotifications() {
		if (!$notificationsEnabled) {
			if ('Notification' in window) {
				const permission = await Notification.requestPermission();
				if (permission === 'granted') {
					notificationsEnabled.set(true);
				} else {
					toast.error($t('general.notificationPermissionDenied'));
				}
			}
		} else {
			notificationsEnabled.set(false);
		}
	}

	async function saveProcessSetting(key: keyof GrokProcessSettings, input: HTMLInputElement) {
		if (!processSettings) return;
		const value = Number(input.value);
		if (input.value.trim() && Number.isInteger(value) && value >= 0) {
			try {
				processSettings = await updateGrokProcessSettings({ [key]: value });
				toast.success($t('settings.saved'));
			} catch {
				toast.error($t('admin.failedToSave'));
			}
		}
		// Show the value the server kept (it caps very large ones).
		input.value = String(processSettings[key]);
	}

	function copyLink() {
		navigator.clipboard.writeText(REPO_URL);
		copied = true;
		setTimeout(() => (copied = false), 2000);
	}

	async function resetPwa() {
		resetting = true;

		try {
			if ('serviceWorker' in navigator) {
				const registrations = await navigator.serviceWorker.getRegistrations();
				await Promise.all(registrations.map((registration) => registration.unregister()));
			}

			if ('caches' in window) {
				const keys = await caches.keys();
				await Promise.all(
					keys.filter((key) => key.startsWith('cptr-')).map((key) => caches.delete(key))
				);
			}
		} finally {
			location.reload();
		}
	}
</script>

<div class="flex flex-col h-full">
	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5 -mr-1.5">
		<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">{$t('general.title')}</h2>

		<div class="mb-5">
			<div class="flex items-baseline gap-2">
				<a
					href={REPO_URL}
					target="_blank"
					rel="noopener noreferrer"
					class="text-xs font-semibold text-gray-900 dark:text-white hover:underline">Computer</a
				>
				{#if $appVersion}
					<button
						onclick={() => showChangelog.set(true)}
						class="text-[0.6875rem] text-gray-400 dark:text-gray-600 hover:text-gray-500 dark:hover:text-gray-400 font-mono hover:underline cursor-pointer"
						>v{$appVersion}</button
					>
				{/if}
				{#if $updateAvailable && $updateCheck?.latest}
					<span class="text-[0.6875rem] text-gray-300 dark:text-gray-600">·</span>
					<button
						onclick={() => showUpdateModal.set(true)}
						class="text-[0.6875rem] text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors cursor-pointer"
						>{$t('about.updateAvailable', { version: $updateCheck.latest.version })}</button
					>
				{/if}
			</div>

			<p class="text-[0.8125rem] text-gray-500 mt-0.5 mb-2">{$t('app.tagline')}</p>

			<div class="flex items-center gap-1.5">
				<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mr-1"
					>{$t('about.share')}</span
				>
				{#each shareLinks as link, i}
					{#if i > 0}
						<span class="text-[0.6875rem] text-gray-200 dark:text-gray-700">·</span>
					{/if}
					<a
						href={link.href}
						target="_blank"
						rel="noopener noreferrer"
						class="text-[0.6875rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-500 transition-colors"
						>{link.label}</a
					>
				{/each}
				<span class="text-[0.6875rem] text-gray-200 dark:text-gray-700">·</span>
				<button
					onclick={copyLink}
					class="text-[0.6875rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-500 transition-colors cursor-pointer"
				>
					{copied ? $t('about.copied') : $t('about.copyLink')}
				</button>
			</div>
		</div>

		<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2">{$t('general.language')}</h3>
		<select
			class="w-full max-w-[12.5rem] bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 outline-none py-1 cursor-pointer"
			value={$locale}
			onchange={(e) => changeLocale((e.currentTarget as HTMLSelectElement).value)}
		>
			{#each supportedLocales as loc}
				<option value={loc.code}>{loc.label}</option>
			{/each}
		</select>

		<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2 mt-5">
			{$t('general.notifications')}
		</h3>
		<div class="flex flex-col gap-2.5">
			<label class="flex items-center justify-between cursor-pointer">
				<span class="text-xs text-gray-600 dark:text-gray-400">
					{$t('general.browserNotifications')}
				</span>
				<ToggleSwitch value={$notificationsEnabled} onchange={() => toggleNotifications()} />
			</label>
			<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 -mt-1">
				{$t('general.browserNotificationsDesc')}
			</p>

			<label class="flex items-center justify-between cursor-pointer">
				<span class="text-xs text-gray-600 dark:text-gray-400">
					{$t('general.notificationSound')}
				</span>
				<ToggleSwitch value={$notificationSound} onchange={(v) => notificationSound.set(v)} />
			</label>
		</div>

		{#if $session?.role === 'admin'}
			<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2 mt-5">{$t('general.updates')}</h3>
			<div class="flex items-center justify-between mb-2.5">
				<span class="text-xs text-gray-600 dark:text-gray-400">
					{$t('general.checkUpdatesDesc')}
				</span>
				<button
					class="app-muted app-interactive h-7 shrink-0 rounded-lg border px-2.5 text-xs font-medium transition-colors duration-75"
					style="border-color: var(--app-border);"
					onclick={() => showUpdateModal.set(true)}>{$t('sidebar.checkUpdates')}</button
				>
			</div>
			<label class="flex items-center justify-between cursor-pointer">
				<span class="text-xs text-gray-600 dark:text-gray-400"
					>{$t('general.updateNotifications')}</span
				>
				<ToggleSwitch value={$showUpdateToastPref} onchange={(v) => showUpdateToastPref.set(v)} />
			</label>
			<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-1">
				{$t('general.updateNotificationsDesc')}
			</p>
		{/if}

		<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2 mt-5">{$t('general.messageQueue')}</h3>
		<div class="flex gap-1">
			{#each [{ value: 'queue' as StreamingBehavior, label: $t('general.queue') }, { value: 'interrupt' as StreamingBehavior, label: $t('general.interrupt') }] as opt}
				<button
					class="flex items-center gap-1.5 h-7 px-2.5 rounded-lg text-xs transition-colors duration-100
					{$streamingBehavior === opt.value
						? 'bg-gray-200/50 dark:bg-white/8 text-gray-900 dark:text-white font-medium'
						: 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'}"
					onclick={() => streamingBehavior.set(opt.value)}
				>
					{opt.label}
				</button>
			{/each}
		</div>
		<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-1">
			{$streamingBehavior === 'queue' ? $t('general.queueDesc') : $t('general.interruptDesc')}
		</p>

		{#if $session?.role === 'admin' && processSettings}
			<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2 mt-5">
				{$t('general.grokProcesses')}
			</h3>
			<div class="flex flex-col gap-2">
				{#each [{ key: 'max_idle_processes' as const, label: $t('general.maxIdleProcesses'), max: 100 }, { key: 'idle_timeout_minutes' as const, label: $t('general.idleTimeoutMinutes'), max: 10080 }] as field (field.key)}
					<label class="flex items-center justify-between gap-3">
						<span class="text-xs text-gray-600 dark:text-gray-400">{field.label}</span>
						<input
							type="number"
							min="0"
							max={field.max}
							step="1"
							value={processSettings[field.key]}
							onchange={(e) => saveProcessSetting(field.key, e.currentTarget)}
							class="w-20 h-7 px-2 rounded-lg text-xs text-right bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none transition-colors"
						/>
					</label>
				{/each}
			</div>
			<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-1">
				{$t('general.grokProcessesDesc')}
			</p>
		{/if}

		{#if $session?.role === 'admin' && retention}
			<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2 mt-5">
				{$t('general.chatRetention')}
			</h3>
			<label class="flex items-center justify-between gap-3">
				<span class="text-xs text-gray-600 dark:text-gray-400"
					>{$t('general.chatRetentionDays')}</span
				>
				<input
					type="number"
					min="0"
					max={retention.max_days}
					step="1"
					value={retention.days}
					onchange={(e) => saveRetention(e.currentTarget)}
					class="w-20 h-7 px-2 rounded-lg text-xs text-right bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none transition-colors"
				/>
			</label>
			<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-1">
				{$t('general.chatRetentionDesc')}
			</p>
		{/if}

		{#if $session?.role === 'admin' && autoGitignoreDotCptr !== null}
			<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2 mt-5">{$t('admin.workspace')}</h3>
			<label class="flex items-center justify-between cursor-pointer">
				<span class="text-xs text-gray-600 dark:text-gray-400"
					>{$t('admin.workspaceAutoGitignoreDotCptr')}</span
				>
				<ToggleSwitch
					value={autoGitignoreDotCptr}
					onchange={saveAutoGitignore}
					disabled={savingWorkspace}
				/>
			</label>
			<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-1">
				{$t('admin.workspaceAutoGitignoreDotCptrHint')}
			</p>
		{/if}

		<div class="pt-5">
			<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-1">
				{$t('about.license')}
			</h3>
			<p class="text-[0.6875rem] text-gray-500 leading-relaxed font-mono whitespace-pre-line">
				<a
					href="https://github.com/open-webui/computer/blob/main/LICENSE"
					target="_blank"
					rel="noopener noreferrer"
					class="underline hover:text-gray-700 dark:hover:text-gray-300"
					>{$t('about.licenseName')}</a
				>

				<br />
				{$t('about.copyright')}
			</p>
			<p class="text-[0.6875rem] text-gray-300 dark:text-gray-700 pt-4">
				{$t('about.createdBy')}
			</p>

			{#if showPwaSettings}
				<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2 mt-5">{$t('pwa.resetTitle')}</h3>
				<button
					class="text-[0.8125rem] text-gray-500 dark:text-gray-500 hover:text-gray-900 dark:hover:text-white transition-colors disabled:opacity-40 disabled:pointer-events-none"
					onclick={resetPwa}
					disabled={resetting}
				>
					{resetting ? $t('pwa.resetting') : $t('pwa.reset')}
				</button>
				<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-1">
					{$t('pwa.resetDesc')}
				</p>
			{/if}
		</div>
	</div>
</div>
