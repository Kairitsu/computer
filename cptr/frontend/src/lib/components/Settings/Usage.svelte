<script lang="ts">
	/**
	 * Settings → Usage, after Grok App's official-account page: a hero card with
	 * the Grok CLI login and SuperGrok quota, then an activity heatmap of this
	 * app's chats with lifetime stats.
	 */
	import { onDestroy, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import CodeBuddyMark from '$lib/components/brand/CodeBuddyMark.svelte';
	import GrokMark from '$lib/components/brand/GrokMark.svelte';
	import Icon from '../Icon.svelte';
	import BudgetSettings from './BudgetSettings.svelte';
	import UsageHeatmap from './UsageHeatmap.svelte';
	import { ApiError } from '$lib/apis';
	import { getUsage, type UsageResponse } from '$lib/apis/chat';
	import {
		cancelGrokLogin,
		getGrokAccount,
		getGrokLogin,
		logoutGrok,
		startGrokLogin,
		submitGrokLoginCode,
		type GrokAccount,
		type GrokLoginStatus
	} from '$lib/apis/grok';
	import { locale, t } from '$lib/i18n';
	import { requestConfirm } from '$lib/stores/confirm';
	import { isSuperGrokQuota, quotaData, refreshQuota } from '$lib/stores/quota';
	import { tooltip } from '$lib/tooltip';
	import {
		formatCount,
		formatDays,
		formatResetTime,
		formatStatDuration
	} from '$lib/utils/usageFormat';

	const USAGE_URL = 'https://grok.com/?_s=usage';
	const LOGIN_POLL_MS = 1500;

	let account = $state<GrokAccount | null>(null);
	let accountLoading = $state(true);
	let usage = $state<UsageResponse | null>(null);
	let usageLoading = $state(true);
	let busy = $state(false);
	let refreshing = $state(false);
	let pasteOpen = $state(false);
	let pasteCode = $state('');
	let loginError = $state('');
	let pollTimer: ReturnType<typeof setInterval> | null = null;

	const profile = $derived(account?.profile ?? null);
	const signedIn = $derived(!!profile?.signed_in);
	const canManage = $derived(!!account?.can_manage);
	const login = $derived<GrokLoginStatus>(account?.login ?? { state: 'idle' });
	const loginRunning = $derived(login.state === 'running');
	const supergrok = $derived(account?.supergrok ?? null);
	const quota = $derived(isSuperGrokQuota(supergrok) ? supergrok : null);
	const quotaError = $derived(supergrok && 'error' in supergrok ? supergrok.error : null);
	/**
	 * The access token's expiry passes routinely while the CLI keeps refreshing
	 * it, so only flag it when the quota can't be read with it either.
	 */
	const credentialsExpired = $derived(signedIn && !!profile?.expired && !quota);
	const planName = $derived(supergrok?.plan || (quota ? 'SuperGrok' : 'Grok Build'));
	const usedPercent = $derived(quota ? Math.min(100, Math.max(0, quota.used_percent)) : null);
	const products = $derived(
		(quota?.products ?? []).filter((p) => p.used_percent > 0 || p.id === 1 || p.id === 2)
	);
	const displayName = $derived(
		signedIn ? profile?.display_name || profile?.email || 'Grok' : $t('grokAccount.signedOutName')
	);
	const codebuddy = $derived($quotaData?.codebuddy ?? null);
	const codebuddyBalance = $derived(codebuddy?.balance ?? null);
	const hasSamples = $derived(
		!!usage && usage.heatmap.some((day) => day.tokens > 0 || day.messages > 0)
	);

	onMount(() => {
		void loadAccount();
		void loadUsage();
	});

	onDestroy(stopPolling);

	async function loadAccount(refresh = false) {
		try {
			account = await getGrokAccount(refresh);
			if (account.login.state === 'running') startPolling();
		} catch (error) {
			toast.error(errorText(error));
		} finally {
			accountLoading = false;
		}
	}

	async function loadUsage() {
		try {
			usage = await getUsage();
		} catch {
			usage = null;
			toast.error($t('usage.failedToLoad'));
		} finally {
			usageLoading = false;
		}
	}

	async function refreshAll() {
		refreshing = true;
		await Promise.all([loadAccount(true), loadUsage(), refreshQuota(true)]);
		refreshing = false;
	}

	function errorText(error: unknown) {
		return error instanceof ApiError || error instanceof Error ? error.message : String(error);
	}

	async function run(action: () => Promise<void>) {
		busy = true;
		try {
			await action();
		} catch (error) {
			toast.error(errorText(error));
		} finally {
			busy = false;
		}
	}

	// ── Sign in ─────────────────────────────────────────────

	function startPolling() {
		if (pollTimer) return;
		pollTimer = setInterval(pollLogin, LOGIN_POLL_MS);
	}

	function stopPolling() {
		if (pollTimer) clearInterval(pollTimer);
		pollTimer = null;
	}

	async function pollLogin() {
		let status;
		try {
			status = await getGrokLogin();
		} catch {
			return;
		}
		if (account) account = { ...account, login: status };
		if (status.state === 'running') return;
		stopPolling();
		pasteOpen = false;
		pasteCode = '';
		if (status.state === 'succeeded') {
			toast.success($t('grokAccount.signedInAs', { name: status.message || 'Grok' }));
			await Promise.all([loadAccount(true), refreshQuota(true)]);
		} else if (status.state === 'failed' || status.state === 'expired') {
			loginError = status.message || $t('grokAccount.loginFailed');
		}
	}

	function startLogin(method: 'oauth' | 'device') {
		loginError = '';
		return run(async () => {
			const status = await startGrokLogin(method);
			if (account) account = { ...account, login: status };
			if (status.state === 'running') startPolling();
		});
	}

	function cancelLogin() {
		return run(async () => {
			const status = await cancelGrokLogin();
			stopPolling();
			if (account) account = { ...account, login: status };
		});
	}

	function submitCode() {
		const code = pasteCode.trim();
		if (!code) return;
		return run(async () => {
			await submitGrokLoginCode(code);
			pasteCode = '';
		});
	}

	async function copy(text: string) {
		try {
			await navigator.clipboard.writeText(text);
			toast.success($t('grokAccount.copied'));
		} catch {
			toast.error($t('grokAccount.copyFailed'));
		}
	}

	// ── Sign out / accounts ─────────────────────────────────

	async function logout() {
		const confirmed = await requestConfirm({
			title: $t('grokAccount.logoutTitle'),
			message: $t('grokAccount.logoutMessage'),
			confirmLabel: $t('grokAccount.logout'),
			cancelLabel: $t('common.cancel')
		});
		if (!confirmed) return;
		await run(async () => {
			await logoutGrok();
			toast.success($t('grokAccount.loggedOut'));
			await Promise.all([loadAccount(true), refreshQuota(true)]);
		});
	}

</script>

<div class="usage-page">
	<h2 class="text-sm font-medium text-gray-900 dark:text-white">{$t('usage.title')}</h2>

	<!-- Hero: identity · actions, then plan + quota, then links -->
	<section class="hero cptr-rise" style="--rise-delay: 40ms;">
		<div class="hero-top">
			<div class="who">
				<div class="avatar" aria-hidden="true">
					<GrokMark size={22} />
				</div>
				<div class="min-w-0 flex-1">
					<div class="name-row">
						<span class="name">{accountLoading ? 'Grok' : displayName}</span>
						{#if !accountLoading && !signedIn}
							<span class="badge">{$t('grokAccount.signedOut')}</span>
						{:else if credentialsExpired}
							<span class="badge">{$t('grokAccount.expired')}</span>
						{/if}
					</div>
					{#if signedIn && profile?.email && profile.email !== displayName}
						<div class="email">{profile.email}</div>
					{/if}
				</div>
			</div>

			<div class="actions">
				{#if signedIn}
					<button type="button" class="settings-action" disabled={refreshing} onclick={refreshAll}>
						{refreshing ? $t('grokAccount.refreshing') : $t('grokAccount.refresh')}
					</button>
					{#if canManage}
						<button type="button" class="settings-action" disabled={busy} onclick={logout}>
							{$t('grokAccount.logout')}
						</button>
					{/if}
				{:else if !accountLoading}
					{#if canManage && !loginRunning}
						<button
							type="button"
							class="settings-action"
							disabled={busy || !account?.cli_found}
							onclick={() => startLogin('oauth')}>{$t('grokAccount.loginOauth')}</button
						>
						<button
							type="button"
							class="settings-action"
							disabled={busy || !account?.cli_found}
							onclick={() => startLogin('device')}>{$t('grokAccount.loginDevice')}</button
						>
					{:else if loginRunning}
						<button type="button" class="settings-action" disabled={busy} onclick={cancelLogin}>
							{$t('common.cancel')}
						</button>
					{:else}
						<button
							type="button"
							class="settings-action"
							disabled={refreshing}
							onclick={refreshAll}
						>
							{$t('grokAccount.refresh')}
						</button>
					{/if}
				{/if}
			</div>
		</div>

		{#if loginRunning}
			<div class="login-panel">
				<strong
					>{login.method === 'device'
						? $t('grokAccount.deviceTitle')
						: $t('grokAccount.oauthTitle')}</strong
				>
				<p>
					{login.method === 'device' ? $t('grokAccount.deviceHint') : $t('grokAccount.oauthHint')}
				</p>
				{#if login.url}
					<div class="flex flex-wrap items-center gap-1.5">
						<a class="settings-action" href={login.url} target="_blank" rel="noopener noreferrer">
							<Icon name="external-link" size={12} />
							{$t('grokAccount.openLoginPage')}
						</a>
						<button type="button" class="settings-action" onclick={() => copy(login.url ?? '')}>
							<Icon name="copy" size={12} />
							{$t('grokAccount.copyLink')}
						</button>
					</div>
				{:else}
					<div class="flex items-center gap-2 text-xs" style="color: var(--app-fg-muted);">
						<Spinner size={12} />
						{$t('grokAccount.loginStarting')}
					</div>
				{/if}
				{#if login.code}
					<button
						type="button"
						class="device-code"
						use:tooltip={$t('common.copy')}
						onclick={() => copy(login.code ?? '')}>{login.code}</button
					>
				{/if}
				{#if login.method === 'oauth'}
					<button
						type="button"
						class="settings-action self-start"
						aria-expanded={pasteOpen}
						onclick={() => (pasteOpen = !pasteOpen)}>{$t('grokAccount.pasteToggle')}</button
					>
					{#if pasteOpen}
						<form
							class="flex items-center gap-1.5"
							onsubmit={(event) => {
								event.preventDefault();
								void submitCode();
							}}
						>
							<input
								class="settings-field min-w-0 flex-1"
								bind:value={pasteCode}
								placeholder={$t('grokAccount.pastePlaceholder')}
								autocomplete="off"
								spellcheck="false"
							/>
							<button type="submit" class="settings-action" disabled={busy || !pasteCode.trim()}
								>{$t('grokAccount.pasteSubmit')}</button
							>
						</form>
					{/if}
				{/if}
			</div>
		{:else if !accountLoading && !signedIn}
			<div class="login-panel" role="note">
				<strong>{$t('grokAccount.loginHelpTitle')}</strong>
				<p>
					{#if !account?.cli_found}
						{$t('grokAccount.cliMissing')}
					{:else if !canManage}
						{$t('grokAccount.adminOnly')}
					{:else}
						{$t('grokAccount.loginHelpBody')}
					{/if}
				</p>
				{#if loginError}
					<p class="login-error">{loginError}</p>
				{/if}
			</div>
		{/if}

		{#if signedIn}
			<div class="plan">
				{#if quota}
					<div class="plan-head">
						<span class="plan-name">{planName}</span>
						<span class="plan-remain"
							>{$t('grokAccount.remaining', {
								percent: Math.round(quota.remaining_percent)
							})}</span
						>
					</div>
					<div class="quota-bar" aria-hidden="true">
						<div
							class="quota-fill"
							class:is-warn={(usedPercent ?? 0) >= 70}
							class:is-danger={(usedPercent ?? 0) >= 90}
							style="width: {usedPercent ?? 0}%;"
						></div>
					</div>
					<div class="plan-meta">
						<span>
							{$t('grokAccount.used', { percent: Math.round(quota.used_percent) })}
							{#if quota.resets_at}
								· {$t('grokAccount.resetsAt', {
									time: formatResetTime(quota.resets_at, $locale)
								})}
							{/if}
						</span>
						{#if products.length}
							<span class="flex flex-wrap gap-1.5">
								{#each products as product (product.id + product.label)}
									<span class="product-tag"
										>{product.label} {Math.round(product.used_percent)}%</span
									>
								{/each}
							</span>
						{/if}
					</div>
				{:else}
					<div class="plan-empty">
						<span class="plan-name">{planName}</span>
						<span class="chip" class:chip-warn={!!quotaError}>
							{quotaError ? $t('grokAccount.quotaError') : $t('grokAccount.quotaUnknown')}
						</span>
						<span class="plan-meta-text">
							{quotaError
								? $t('usageIndicator.supergrokError')
								: $t('grokAccount.quotaUnknownHint')}
						</span>
					</div>
				{/if}
			</div>

			<div class="links">
				<a class="link-btn" href={USAGE_URL} target="_blank" rel="noopener noreferrer"
					>{$t('grokAccount.viewUsage')}</a
				>
			</div>
		{/if}

		{#if !accountLoading && !quota}
			<div class="plan">
				<BudgetSettings />
			</div>
		{/if}
	</section>

	<!-- CodeBuddy: the same quota card as SuperGrok, with its own mark and usage page. -->
	<section class="hero">
		<div class="hero-top">
			<div class="who">
				<div class="avatar" aria-hidden="true">
					<CodeBuddyMark size={32} />
				</div>
				<div class="min-w-0 flex-1">
					<div class="name-row">
						<span class="name">CodeBuddy</span>
						{#if codebuddy && !codebuddy.signed_in}
							<span class="badge">{$t('grokAccount.signedOut')}</span>
						{/if}
					</div>
					{#if codebuddy?.nickname}
						<div class="email">{codebuddy.nickname}</div>
					{/if}
				</div>
			</div>
		</div>

		<div class="plan">
			{#if codebuddyBalance}
				<div class="plan-head">
					<span class="plan-name">CodeBuddy</span>
					<span class="plan-remain"
						>{$t('grokAccount.remaining', {
							percent: Math.round(codebuddyBalance.remaining_percent)
						})}</span
					>
				</div>
				<div class="quota-bar" aria-hidden="true">
					<div
						class="quota-fill"
						class:is-warn={codebuddyBalance.used_percent >= 70}
						class:is-danger={codebuddyBalance.used_percent >= 90}
						style="width: {Math.min(100, Math.max(0, codebuddyBalance.used_percent))}%;"
					></div>
				</div>
				<div class="plan-meta">
					<span>
						{$t('grokAccount.used', { percent: Math.round(codebuddyBalance.used_percent) })}
						{#if codebuddyBalance.resets_at}
							· {$t('grokAccount.resetsAt', {
								time: formatResetTime(codebuddyBalance.resets_at, $locale)
							})}
						{/if}
					</span>
				</div>
			{:else}
				<div class="plan-empty">
					<span class="plan-name">CodeBuddy</span>
					<span class="chip" class:chip-warn={!!codebuddy?.error}>
						{codebuddy?.signed_in
							? $t('grokAccount.quotaError')
							: $t('grokAccount.quotaUnknown')}
					</span>
					<span class="plan-meta-text">
						{codebuddy?.signed_in
							? $t('usageIndicator.codebuddyQuotaError')
							: $t('usageIndicator.codebuddySignedOut')}
					</span>
				</div>
			{/if}
		</div>

		{#if codebuddy?.usage_url}
			<div class="links">
				<a class="link-btn" href={codebuddy.usage_url} target="_blank" rel="noopener noreferrer"
					>{$t('grokAccount.viewUsage')}</a
				>
			</div>
		{/if}
	</section>

	<!-- Activity heatmap -->
	<section class="activity">
		<div class="section-title cptr-rise" style="--rise-delay: 120ms;">
			<span>{$t('usage.heatmap')}</span>
		</div>

		{#if usage && hasSamples}
			<div class="stats cptr-rise" style="--rise-delay: 180ms;" aria-live="polite">
				<div class="stat" title={usage.totals.lifetime_tokens.toLocaleString($locale)}>
					<span class="stat-value">{formatCount(usage.totals.lifetime_tokens, $locale)}</span>
					<span class="stat-label">{$t('usage.lifetimeTokens')}</span>
				</div>
				<div class="stat" title={usage.totals.peak_daily_tokens.toLocaleString($locale)}>
					<span class="stat-value">{formatCount(usage.totals.peak_daily_tokens, $locale)}</span>
					<span class="stat-label">{$t('usage.peakTokens')}</span>
				</div>
				<div class="stat">
					<span class="stat-value"
						>{formatStatDuration(usage.totals.longest_chat_seconds, $locale)}</span
					>
					<span class="stat-label">{$t('usage.longestActiveChat')}</span>
				</div>
				<div class="stat">
					<span class="stat-value">{formatDays(usage.totals.current_streak, $locale)}</span>
					<span class="stat-label">{$t('usage.currentStreak')}</span>
				</div>
				<div class="stat">
					<span class="stat-value">{formatDays(usage.totals.longest_streak, $locale)}</span>
					<span class="stat-label">{$t('usage.longestStreak')}</span>
				</div>
			</div>
		{/if}

		<div class="heat-card cptr-rise" style="--rise-delay: 240ms;">
			{#if usageLoading}
				<div class="flex items-center justify-center py-10"><Spinner size={18} /></div>
			{:else if !usage}
				<div class="empty">{$t('usage.failedToLoad')}</div>
			{:else if !hasSamples}
				<div class="empty">
					<div class="empty-title">{$t('usage.noData')}</div>
					<div>{$t('usage.noDataHint')}</div>
				</div>
			{:else}
				<UsageHeatmap days={usage.heatmap} />
			{/if}
		</div>
		<p class="note">{$t('usage.estimateNote')}</p>
	</section>
</div>

<style>
	.usage-page {
		--card-border: color-mix(in oklab, var(--app-fg) 10%, transparent);
		--card-bg: color-mix(in oklab, var(--app-fg) 1.5%, var(--app-bg));
		display: flex;
		flex-direction: column;
		gap: 1rem;
		padding-bottom: 0.5rem;
	}

	/* ── Hero ── */
	.hero {
		display: flex;
		flex-direction: column;
		gap: 0.875rem;
		padding: 0.875rem 1rem;
		border: 1px solid var(--card-border);
		border-radius: 0.75rem;
		background: var(--card-bg);
	}

	.hero-top {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem 1rem;
		min-width: 0;
	}

	.who {
		display: flex;
		flex: 1 1 11rem;
		align-items: center;
		gap: 0.75rem;
		min-width: 0;
	}

	.avatar {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 2.5rem;
		height: 2.5rem;
		flex-shrink: 0;
		border-radius: 999px;
		color: var(--app-accent);
		background: color-mix(in oklab, var(--app-accent) 12%, transparent);
		border: 1px solid var(--card-border);
	}

	.name-row {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		min-width: 0;
	}

	.name {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 0.9375rem;
		font-weight: 600;
		color: var(--app-fg);
	}

	.email {
		margin-top: 0.125rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 0.75rem;
		color: var(--app-fg-subtle);
	}

	.badge {
		flex-shrink: 0;
		padding: 0.0625rem 0.5rem;
		border-radius: 999px;
		font-size: 0.6875rem;
		font-weight: 500;
		color: var(--app-fg-subtle);
		background: var(--app-hover);
		border: 1px solid var(--card-border);
	}

	.actions {
		display: flex;
		flex-wrap: wrap;
		flex-shrink: 0;
		align-items: center;
		justify-content: flex-end;
		gap: 0.375rem;
	}

	.login-panel {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		padding: 0.625rem 0.75rem;
		border-radius: 0.625rem;
		font-size: 0.75rem;
		color: var(--app-fg-muted);
		background: color-mix(in oklab, var(--app-fg) 4%, transparent);
		border: 1px solid var(--card-border);
	}

	.login-panel strong {
		color: var(--app-fg);
		font-weight: 600;
	}

	.login-panel p {
		margin: 0;
		line-height: 1.5;
	}

	.login-error {
		color: #d97706;
	}

	.device-code {
		align-self: flex-start;
		padding: 0.375rem 0.875rem;
		border: 0;
		border-radius: 0.75rem;
		font-family: var(--font-mono);
		font-size: 1.125rem;
		font-weight: 600;
		letter-spacing: 0.12em;
		color: var(--app-fg);
		background: color-mix(in oklab, var(--app-fg) 6%, transparent);
		transition:
			background 0.1s,
			box-shadow 0.1s;
	}

	.device-code:hover,
	.device-code:focus-visible {
		background: var(--app-bg);
		box-shadow: 0 1px 3px color-mix(in oklab, black 10%, transparent);
	}

	.plan {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		min-width: 0;
		padding-top: 0.75rem;
		border-top: 1px solid var(--card-border);
	}

	.plan-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 0.75rem;
	}

	.plan-name {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 0.875rem;
		font-weight: 600;
		color: var(--app-fg);
	}

	.plan-remain {
		flex-shrink: 0;
		font-size: 0.8125rem;
		font-weight: 500;
		font-variant-numeric: tabular-nums;
		color: var(--app-fg-muted);
	}

	.quota-bar {
		height: 0.375rem;
		overflow: hidden;
		border-radius: 999px;
		background: color-mix(in oklab, var(--app-fg) 8%, transparent);
	}

	.quota-fill {
		height: 100%;
		border-radius: inherit;
		background: var(--app-accent);
		transform-origin: left center;
		animation: bar-grow 900ms cubic-bezier(0.22, 1, 0.36, 1) 200ms both;
		transition: width 0.3s ease;
	}

	.quota-fill.is-warn {
		background: #d97706;
	}

	.quota-fill.is-danger {
		background: #dc2626;
	}

	@keyframes bar-grow {
		from {
			transform: scaleX(0);
		}
	}

	.plan-meta {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 0.5rem 0.75rem;
		font-size: 0.75rem;
		font-variant-numeric: tabular-nums;
		color: var(--app-fg-subtle);
	}

	.product-tag {
		padding: 0.125rem 0.5rem;
		border-radius: 999px;
		font-size: 0.6875rem;
		color: var(--app-fg-muted);
		background: var(--app-hover);
		border: 1px solid var(--card-border);
	}

	.plan-empty {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem 0.875rem;
	}

	.plan-meta-text {
		font-size: 0.75rem;
		color: var(--app-fg-subtle);
	}

	.chip {
		padding: 0.0625rem 0.5rem;
		border-radius: 999px;
		font-size: 0.6875rem;
		color: var(--app-fg-muted);
		background: var(--app-hover);
	}

	.chip-warn {
		color: #b45309;
		background: color-mix(in oklab, #f59e0b 14%, transparent);
	}

	.links {
		display: flex;
		flex-wrap: wrap;
		gap: 0.875rem;
	}

	.link-btn {
		padding: 0;
		font-size: 0.75rem;
		color: var(--app-accent);
		background: none;
		border: none;
	}

	.link-btn:hover {
		text-decoration: underline;
	}

	/* ── Activity ── */
	.activity {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		min-width: 0;
		container-type: inline-size;
	}

	.section-title {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		font-size: 0.75rem;
		font-weight: 600;
		color: var(--app-fg-muted);
	}

	.stats {
		display: grid;
		grid-template-columns: repeat(5, minmax(0, 1fr));
		overflow: hidden;
		border: 1px solid var(--card-border);
		border-radius: 0.75rem;
		background: var(--card-bg);
	}

	.stat {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 0.25rem;
		min-width: 0;
		padding: 0.875rem 0.5rem;
		text-align: center;
		border-right: 1px solid var(--card-border);
	}

	.stat:last-child {
		border-right: none;
	}

	.stat-value {
		max-width: 100%;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 1rem;
		font-weight: 650;
		line-height: 1.2;
		letter-spacing: -0.01em;
		font-variant-numeric: tabular-nums;
		color: var(--app-fg);
	}

	.stat-label {
		font-size: 0.6875rem;
		line-height: 1.3;
		white-space: nowrap;
		color: var(--app-fg-subtle);
	}

	@container (max-width: 30rem) {
		.stats {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}

		.stat {
			border-bottom: 1px solid var(--card-border);
		}

		.stat:nth-child(2n) {
			border-right: none;
		}

		.stat:last-child {
			grid-column: span 2;
			border-bottom: none;
		}
	}

	.heat-card {
		min-width: 0;
		padding: 0.875rem 1rem 0.75rem;
		border: 1px solid var(--card-border);
		border-radius: 0.75rem;
		background: var(--card-bg);
	}

	.empty {
		padding: 1.5rem 0.5rem;
		text-align: center;
		font-size: 0.75rem;
		line-height: 1.5;
		color: var(--app-fg-subtle);
	}

	.empty-title {
		margin-bottom: 0.25rem;
		font-size: 0.8125rem;
		font-weight: 600;
		color: var(--app-fg-muted);
	}

	.note {
		margin: 0;
		text-align: right;
		font-size: 0.6875rem;
		color: var(--app-fg-subtle);
	}

	@media (prefers-reduced-motion: reduce) {
		.quota-fill {
			animation: none;
		}
	}
</style>
