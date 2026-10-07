<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { login, setup, signup } from '$lib/apis/auth';
	import { ApiError } from '$lib/apis';
	import { t } from '$lib/i18n';
	import Spinner from '$lib/components/common/Spinner.svelte';

	interface Props {
		mode: 'password' | 'pam';
		needsSetup?: boolean;
		signupEnabled?: boolean;
		token?: string;
		onauth: () => void;
	}

	let { mode, needsSetup, signupEnabled = false, token = '', onauth }: Props = $props();

	// The sign-in page is always light, whatever appearance the app itself uses.
	const PAGE_BG = '#f4f4f5';

	let username = $state('');
	let password = $state('');
	let loading = $state(false);
	let isSignup = $state(false);

	const isSetup = mode === 'password' && needsSetup;

	const subtitle = $derived(
		isSetup || isSignup
			? $t('auth.createAccountHint')
			: mode === 'pam'
				? $t('auth.signInSystemHint')
				: $t('app.tagline')
	);

	// The button draws its own arrow, so drop the one in the translation.
	const submitLabel = $derived(
		(isSetup ? $t('auth.createAccountBtn') : isSignup ? $t('auth.signUpBtn') : $t('auth.signInBtn'))
			.replace(/\s*→\s*$/, '')
			.trim()
	);

	onMount(() => {
		// Match the browser chrome (mobile address bar, PWA title bar) to the page.
		const meta = document.querySelector('meta[name="theme-color"]');
		const previous = meta?.getAttribute('content');
		meta?.setAttribute('content', PAGE_BG);
		return () => {
			if (meta && previous) meta.setAttribute('content', previous);
		};
	});

	async function submit() {
		if (isSetup && password.length < 6) {
			toast.error($t('auth.minChars'));
			return;
		}
		if (!username.trim()) {
			toast.error($t('auth.usernameRequired'));
			return;
		}
		if (!password) {
			toast.error($t('auth.passwordRequired'));
			return;
		}

		loading = true;
		try {
			if (isSetup) {
				await setup(username.trim(), password, token);
				onauth();
			} else if (isSignup) {
				const data = await signup(username.trim(), password);
				if (data.pending) {
					toast.success($t('auth.accountPending'));
					isSignup = false;
					username = '';
					password = '';
				} else {
					onauth();
				}
			} else {
				await login(username.trim(), password);
				onauth();
			}
		} catch (e) {
			const msg = e instanceof ApiError ? e.message : $t('auth.connectionFailed');
			toast.error(msg);
			password = '';
		} finally {
			loading = false;
		}
	}

	function handleSubmit(e: SubmitEvent) {
		e.preventDefault();
		submit();
	}
</script>

<div class="auth-root" style="--auth-page-bg: {PAGE_BG};">
	<main class="auth-card">
		<header class="auth-header">
			<div class="auth-logo" role="img" aria-label={$t('app.logoAlt')}>
				<svg viewBox="0 0 200 148" aria-hidden="true">
					<circle cx="74" cy="74" r="59.5" fill="none" stroke="currentColor" stroke-width="29" />
					<rect x="171" y="0" width="29" height="148" fill="currentColor" />
				</svg>
			</div>
			<h1 class="auth-title">Computer</h1>
			<p class="auth-subtitle">{subtitle}</p>
		</header>

		<form class="auth-form" onsubmit={handleSubmit} action="javascript:void(0)">
			<label class="auth-field">
				<span class="auth-label">{$t('auth.username')}</span>
				<input
					type="text"
					class="auth-input"
					bind:value={username}
					autofocus
					autocomplete="username"
					autocapitalize="off"
					spellcheck="false"
				/>
			</label>
			<label class="auth-field">
				<span class="auth-label">{$t('auth.password')}</span>
				<input
					type="password"
					class="auth-input"
					bind:value={password}
					autocomplete={isSetup || isSignup ? 'new-password' : 'current-password'}
				/>
			</label>

			<button type="submit" class="auth-submit" disabled={loading || !password || !username.trim()}>
				{#if loading}
					<Spinner size={18} />
				{:else}
					<span>{submitLabel}</span>
					<svg class="auth-submit-arrow" viewBox="0 0 20 20" fill="none" aria-hidden="true">
						<path
							d="M4 10h11m-4.5-4.5L15 10l-4.5 4.5"
							stroke="currentColor"
							stroke-width="1.8"
							stroke-linecap="round"
							stroke-linejoin="round"
						/>
					</svg>
				{/if}
			</button>
		</form>

		{#if !isSetup && signupEnabled && mode === 'password'}
			<p class="auth-switch">
				{#if isSignup}
					{$t('auth.alreadyHaveAccount')}
					<button
						type="button"
						onclick={() => {
							isSignup = false;
							password = '';
						}}>{$t('auth.signInLink')}</button
					>
				{:else}
					{$t('auth.dontHaveAccount')}
					<button
						type="button"
						onclick={() => {
							isSignup = true;
							password = '';
						}}>{$t('auth.signUpLink')}</button
					>
				{/if}
			</p>
		{/if}
	</main>
</div>

<style>
	.auth-root {
		--auth-ink: #0a0a0a;
		--auth-text: #262626;
		--auth-muted: #525252;
		--auth-line: #d4d4d8;
		--auth-line-strong: #a1a1aa;

		position: fixed;
		inset: 0;
		display: flex;
		overflow-y: auto;
		padding: max(1.5rem, env(safe-area-inset-top)) 1rem max(1.5rem, env(safe-area-inset-bottom));
		font-size: 1rem;
		font-family: var(--font-sans);
		color: var(--auth-text);
		color-scheme: light;
		-webkit-font-smoothing: antialiased;
		background:
			radial-gradient(60rem 36rem at 50% -12%, #ffffff 0%, rgba(255, 255, 255, 0) 70%),
			radial-gradient(
				40rem 30rem at 100% 110%,
				rgba(228, 228, 231, 0.7) 0%,
				rgba(228, 228, 231, 0) 70%
			),
			var(--auth-page-bg);
	}

	/* Everything below is sized in em, so on large displays the card grows as a whole. */
	@media (min-width: 1440px) and (min-height: 820px) {
		.auth-root {
			font-size: 1.125rem;
		}
	}

	.auth-card {
		margin: auto;
		width: 100%;
		max-width: 26em;
		padding: 2.5em 2.25em 2.25em;
		border-radius: 1.75em;
		background: #ffffff;
		box-shadow:
			0 0 0 1px rgba(24, 24, 27, 0.05),
			0 1px 2px rgba(24, 24, 27, 0.04),
			0 8px 24px -6px rgba(24, 24, 27, 0.08),
			0 32px 64px -24px rgba(24, 24, 27, 0.18);
		animation: auth-rise 520ms cubic-bezier(0.22, 1, 0.36, 1) both;
	}

	.auth-header {
		display: flex;
		flex-direction: column;
		align-items: center;
		text-align: center;
		margin-bottom: 2em;
	}

	.auth-logo {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 4.5em;
		height: 4.5em;
		margin-bottom: 1.25em;
		border-radius: 1.125em;
		color: var(--auth-ink);
		background: linear-gradient(180deg, #ffffff 0%, #f6f6f7 100%);
		box-shadow:
			inset 0 1px 0 rgba(255, 255, 255, 0.9),
			0 0 0 1px rgba(24, 24, 27, 0.06),
			0 2px 4px rgba(24, 24, 27, 0.05),
			0 12px 28px -8px rgba(24, 24, 27, 0.22);
	}

	.auth-logo svg {
		width: 2.25em;
		height: auto;
	}

	.auth-title {
		margin: 0;
		font-size: 1.875em;
		font-weight: 700;
		line-height: 1.2;
		letter-spacing: -0.025em;
		color: var(--auth-ink);
	}

	.auth-subtitle {
		margin: 0.5em 0 0;
		font-size: 1em;
		font-weight: 500;
		line-height: 1.5;
		color: var(--auth-muted);
	}

	.auth-form {
		display: flex;
		flex-direction: column;
		gap: 1.125em;
	}

	.auth-field {
		display: flex;
		flex-direction: column;
		gap: 0.5em;
	}

	.auth-label {
		display: block;
		font-size: 0.9375em;
		font-weight: 600;
		line-height: 1.3;
		color: var(--auth-text);
	}

	.auth-label::first-letter {
		text-transform: uppercase;
	}

	.auth-input {
		display: block;
		width: 100%;
		height: 3em;
		padding: 0 1em;
		border: 1px solid var(--auth-line);
		border-radius: 0.875em;
		/* 16px or more keeps iOS from zooming into the field. */
		font-size: max(16px, 1.0625em);
		font-weight: 500;
		color: var(--auth-ink);
		caret-color: var(--auth-ink);
		background: #fafafa;
		box-shadow: inset 0 1px 2px rgba(24, 24, 27, 0.04);
		outline: none;
		transition:
			border-color 150ms ease,
			background-color 150ms ease,
			box-shadow 150ms ease;
	}

	.auth-input:hover {
		border-color: var(--auth-line-strong);
	}

	.auth-input:focus {
		border-color: var(--auth-ink);
		background: #ffffff;
		box-shadow: 0 0 0 4px rgba(10, 10, 10, 0.08);
	}

	/* Keep browser autofill from painting the field yellow or blue. */
	.auth-input:-webkit-autofill {
		-webkit-text-fill-color: var(--auth-ink);
		transition: background-color 600000s 0s;
	}

	.auth-submit {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 0.5em;
		width: 100%;
		height: 3.125em;
		margin-top: 0.625em;
		border: none;
		border-radius: 0.875em;
		font-size: 1.0625em;
		font-weight: 600;
		letter-spacing: 0.01em;
		color: #ffffff;
		background: linear-gradient(180deg, #27272a 0%, #0a0a0a 100%);
		box-shadow:
			inset 0 1px 0 rgba(255, 255, 255, 0.12),
			0 1px 2px rgba(10, 10, 10, 0.2),
			0 8px 20px -8px rgba(10, 10, 10, 0.45);
		transition:
			transform 150ms ease,
			box-shadow 150ms ease,
			opacity 150ms ease;
	}

	.auth-submit:hover:not(:disabled) {
		transform: translateY(-1px);
		box-shadow:
			inset 0 1px 0 rgba(255, 255, 255, 0.12),
			0 2px 4px rgba(10, 10, 10, 0.2),
			0 14px 28px -10px rgba(10, 10, 10, 0.5);
	}

	.auth-submit:active:not(:disabled) {
		transform: translateY(0) scale(0.99);
	}

	.auth-submit:focus-visible {
		outline: none;
		box-shadow:
			0 0 0 3px #ffffff,
			0 0 0 5px rgba(10, 10, 10, 0.35);
	}

	.auth-submit:disabled {
		cursor: default;
		opacity: 0.4;
		box-shadow: none;
	}

	.auth-submit-arrow {
		width: 1.125em;
		height: 1.125em;
		transition: transform 150ms ease;
	}

	.auth-submit:hover:not(:disabled) .auth-submit-arrow {
		transform: translateX(2px);
	}

	.auth-root .auth-submit :global(.spinner) {
		border-color: rgba(255, 255, 255, 0.3);
		border-top-color: #ffffff;
	}

	.auth-switch {
		margin: 1.5em 0 0;
		text-align: center;
		font-size: 0.9375em;
		font-weight: 500;
		color: var(--auth-muted);
	}

	.auth-switch button {
		margin-left: 0.25em;
		padding: 0;
		border: none;
		background: none;
		font: inherit;
		font-weight: 600;
		color: var(--auth-ink);
		text-decoration: underline;
		text-decoration-color: transparent;
		text-underline-offset: 0.2em;
		transition: text-decoration-color 150ms ease;
	}

	.auth-switch button:hover {
		text-decoration-color: currentColor;
	}

	@media (max-width: 480px) {
		.auth-card {
			padding: 2em 1.5em 1.75em;
			border-radius: 1.5em;
		}
	}

	@keyframes auth-rise {
		from {
			opacity: 0;
			transform: translateY(12px) scale(0.985);
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.auth-card {
			animation: none;
		}
	}
</style>
