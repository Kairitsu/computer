<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		verifyCurrentPassword,
		verifyPasswordCode,
		updatePassword as apiUpdatePassword,
		uploadAvatar,
		deleteAvatar,
		updateProfile,
		getTotpStatus,
		setupTotp,
		enableTotp,
		disableTotp,
		type TotpStatus,
		type TotpSetup
	} from '$lib/apis/auth';
	import { ApiError } from '$lib/apis';
	import { session, setSession, clearSession } from '$lib/session';
	import { t } from '$lib/i18n';
	import Spinner from '$lib/components/common/Spinner.svelte';

	type PasswordStep = 'idle' | 'current' | 'forgot' | 'new';
	type ForgotMethod = 'totp' | 'recovery';

	let passwordStep = $state<PasswordStep>('idle');
	let forgotMethod = $state<ForgotMethod>('totp');
	let currentPassword = $state('');
	let newPassword = $state('');
	let passwordCode = $state('');
	let passwordGrant = $state('');
	let updatingPassword = $state(false);
	let uploadingAvatar = $state(false);
	let displayName = $state($session?.display_name ?? '');
	let savingProfile = $state(false);

	let avatarUrl = $derived($session?.profile_image_url);

	// Two-step sign-in
	let totpStatus = $state<TotpStatus | null>(null);
	let totpSetup = $state<TotpSetup | null>(null);
	let recoveryCodes = $state<string[] | null>(null);
	let disablingTotp = $state(false);
	let totpPassword = $state('');
	let totpCode = $state('');
	let totpBusy = $state(false);

	// The secret in groups of four, for typing it into an app by hand.
	const groupedSecret = $derived(totpSetup?.secret.match(/.{1,4}/g)?.join(' ') ?? '');

	onMount(loadTotpStatus);

	async function loadTotpStatus() {
		try {
			totpStatus = await getTotpStatus();
		} catch {
			totpStatus = null;
		}
	}

	function totpError(e: unknown) {
		toast.error(
			e instanceof ApiError
				? e.message === 'invalid code'
					? $t('auth.codeInvalid')
					: e.message
				: $t('auth.connectionFailed')
		);
	}

	function cancelTotp() {
		totpSetup = null;
		disablingTotp = false;
		totpPassword = '';
		totpCode = '';
	}

	async function startTotpSetup() {
		totpBusy = true;
		try {
			totpSetup = await setupTotp(totpPassword);
			totpPassword = '';
		} catch (e) {
			totpError(e);
		} finally {
			totpBusy = false;
		}
	}

	async function confirmTotp() {
		totpBusy = true;
		try {
			const result = await enableTotp(totpCode.trim());
			cancelTotp();
			recoveryCodes = result.recovery_codes;
			toast.success($t('account.totpEnabled'));
			await loadTotpStatus();
		} catch (e) {
			totpError(e);
			totpCode = '';
		} finally {
			totpBusy = false;
		}
	}

	async function turnOffTotp() {
		totpBusy = true;
		try {
			await disableTotp(totpPassword, totpCode.trim());
			cancelTotp();
			toast.success($t('account.totpDisabled'));
			await loadTotpStatus();
		} catch (e) {
			totpError(e);
			totpCode = '';
		} finally {
			totpBusy = false;
		}
	}

	async function copyRecoveryCodes() {
		if (!recoveryCodes) return;
		try {
			await navigator.clipboard.writeText(recoveryCodes.join('\n'));
			toast.success($t('account.totpCodesCopied'));
		} catch {
			toast.error($t('account.failedToUpdate'));
		}
	}

	async function saveDisplayName() {
		const trimmed = displayName.trim();
		if (trimmed === ($session?.display_name ?? '')) return;
		savingProfile = true;
		try {
			await updateProfile(trimmed || null);
			if ($session) {
				setSession({ ...$session, display_name: trimmed || null });
			}
			toast.success($t('account.displayNameUpdated'));
		} catch (e) {
			toast.error(e instanceof ApiError ? e.message : $t('account.failedToUpdate'));
		} finally {
			savingProfile = false;
		}
	}

	function resetPasswordFlow() {
		passwordStep = 'idle';
		forgotMethod = 'totp';
		currentPassword = '';
		newPassword = '';
		passwordCode = '';
		passwordGrant = '';
	}

	function passwordError(e: unknown) {
		if (!(e instanceof ApiError)) {
			toast.error($t('auth.connectionFailed'));
			return;
		}
		const known: Record<string, string> = {
			'incorrect current password': 'account.passwordIncorrect',
			'incorrect password': 'account.passwordIncorrect',
			'invalid code': 'auth.codeInvalid',
			'two-step sign-in is off': 'account.passwordForgotUnavailable',
			'min 6 characters': 'auth.minChars',
			'too many attempts': 'account.tooManyAttempts',
			'verification expired': 'account.passwordVerifyExpired'
		};
		const key = known[e.message];
		toast.error(key ? $t(key) : e.message);
	}

	async function verifyCurrent() {
		if (!currentPassword) return;
		updatingPassword = true;
		try {
			const result = await verifyCurrentPassword(currentPassword);
			currentPassword = '';
			passwordGrant = result.grant;
			passwordStep = 'new';
		} catch (e) {
			passwordError(e);
		} finally {
			updatingPassword = false;
		}
	}

	async function verifyForgot() {
		if (!passwordCode.trim()) return;
		updatingPassword = true;
		try {
			const result = await verifyPasswordCode(forgotMethod, passwordCode.trim());
			passwordCode = '';
			passwordGrant = result.grant;
			passwordStep = 'new';
		} catch (e) {
			passwordError(e);
			passwordCode = '';
		} finally {
			updatingPassword = false;
		}
	}

	async function confirmNewPassword() {
		if (!newPassword) return;
		if (newPassword.length < 6) {
			toast.error($t('auth.minChars'));
			return;
		}
		updatingPassword = true;
		try {
			await apiUpdatePassword(passwordGrant, newPassword);
			sessionStorage.setItem('cptr.passwordReset', '1');
			clearSession();
		} catch (e) {
			passwordError(e);
			if (e instanceof ApiError && e.message === 'verification expired') resetPasswordFlow();
		} finally {
			updatingPassword = false;
		}
	}

	/** Resize image client-side to 256×256 before upload. */
	function resizeImage(file: File): Promise<Blob> {
		return new Promise((resolve, reject) => {
			const img = new Image();
			const url = URL.createObjectURL(file);
			img.onload = () => {
				URL.revokeObjectURL(url);
				const canvas = document.createElement('canvas');
				canvas.width = 256;
				canvas.height = 256;
				const ctx = canvas.getContext('2d')!;

				// Center crop to square
				const size = Math.min(img.width, img.height);
				const sx = (img.width - size) / 2;
				const sy = (img.height - size) / 2;
				ctx.drawImage(img, sx, sy, size, size, 0, 0, 256, 256);

				canvas.toBlob(
					(blob) => (blob ? resolve(blob) : reject(new Error('Canvas export failed'))),
					'image/png'
				);
			};
			img.onerror = () => {
				URL.revokeObjectURL(url);
				reject(new Error('Failed to load image'));
			};
			img.src = url;
		});
	}

	async function handleAvatarSelect() {
		const input = document.createElement('input');
		input.type = 'file';
		input.accept = 'image/*';
		input.onchange = async () => {
			const file = input.files?.[0];
			if (!file) return;
			uploadingAvatar = true;
			try {
				const resized = await resizeImage(file);
				const result = await uploadAvatar(resized);
				if ($session) {
					setSession({ ...$session, profile_image_url: result.profile_image_url });
				}
				toast.success($t('account.avatarUpdated'));
			} catch (e) {
				toast.error(e instanceof ApiError ? e.message : $t('account.uploadFailed'));
			} finally {
				uploadingAvatar = false;
			}
		};
		input.click();
	}

	async function handleAvatarDelete() {
		uploadingAvatar = true;
		try {
			await deleteAvatar();
			if ($session) {
				setSession({ ...$session, profile_image_url: null });
			}
			toast.success($t('account.avatarRemoved'));
		} catch (e) {
			toast.error(e instanceof ApiError ? e.message : $t('account.failedToRemoveAvatar'));
		} finally {
			uploadingAvatar = false;
		}
	}
</script>

<div class="flex flex-col min-h-full">
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">{$t('account.title')}</h2>

	<!-- Avatar -->
	<div class="flex items-center gap-4 mb-6">
		<button
			class="relative w-10 h-10 rounded-full overflow-hidden bg-gray-100 dark:bg-white/8 flex items-center justify-center
			ring-1 ring-gray-200 dark:ring-white/10 hover:ring-2 hover:ring-gray-300 dark:hover:ring-white/20 transition-all duration-200 group shrink-0"
			onclick={handleAvatarSelect}
			disabled={uploadingAvatar}
		>
			{#if uploadingAvatar}
				<Spinner size={20} />
			{:else}
				<img src={avatarUrl || '/user.png'} alt="Avatar" class="w-full h-full object-cover" />
				<div
					class="absolute inset-0 bg-black/0 group-hover:bg-black/40 transition-colors duration-200 flex items-center justify-center"
				>
					<svg
						class="w-4 h-4 text-white opacity-0 group-hover:opacity-100 transition-opacity duration-200 drop-shadow-sm"
						fill="none"
						viewBox="0 0 24 24"
						stroke="currentColor"
						stroke-width="1.5"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							d="M6.827 6.175A2.31 2.31 0 0 1 5.186 7.23c-.38.054-.757.112-1.134.175C2.999 7.58 2.25 8.507 2.25 9.574V18a2.25 2.25 0 0 0 2.25 2.25h15A2.25 2.25 0 0 0 21.75 18V9.574c0-1.067-.75-1.994-1.802-2.169a47.865 47.865 0 0 0-1.134-.175 2.31 2.31 0 0 1-1.64-1.055l-.822-1.316a2.192 2.192 0 0 0-1.736-1.039 48.774 48.774 0 0 0-5.232 0 2.192 2.192 0 0 0-1.736 1.039l-.821 1.316Z"
						/>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							d="M16.5 12.75a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0Z"
						/>
					</svg>
				</div>
			{/if}
		</button>
		<div class="flex flex-col gap-1">
			<span class="text-sm font-medium text-gray-900 dark:text-white"
				>{$session?.display_name || $session?.username}</span
			>
			<div class="flex flex-wrap items-center gap-1.5">
				<button
					type="button"
					class="settings-action"
					onclick={handleAvatarSelect}
					disabled={uploadingAvatar}>{$t('account.uploadPhoto')}</button
				>
				{#if avatarUrl}
					<button
						type="button"
						class="settings-action"
						onclick={handleAvatarDelete}
						disabled={uploadingAvatar}>{$t('account.removePhoto')}</button
					>
				{/if}
			</div>
		</div>
	</div>

	<!-- Display Name -->
	<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-1">{$t('account.displayName')}</h3>
	<input
		type="text"
		placeholder={$t('account.displayNamePlaceholder')}
		bind:value={displayName}
		class="settings-field mb-5 w-full"
	/>

	<!-- Password: hidden until the user asks to change it, then verified before the new one. -->
	<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-1">{$t('account.password')}</h3>
	{#if passwordStep === 'idle'}
		<button
			type="button"
			class="settings-action self-start"
			onclick={() => (passwordStep = 'current')}
		>
			{$t('account.changePassword')}
		</button>
	{:else if passwordStep === 'current'}
		<input
			type="password"
			placeholder={$t('account.currentPassword')}
			bind:value={currentPassword}
			autocomplete="current-password"
			class="settings-field w-full"
			onkeydown={(e) => {
				if (e.key === 'Enter') verifyCurrent();
			}}
		/>
		<div class="mt-2 flex flex-wrap gap-1.5">
			<button
				type="button"
				class="settings-action"
				disabled={updatingPassword || !currentPassword}
				onclick={verifyCurrent}
			>
				{#if updatingPassword}<Spinner size={14} />{:else}{$t('account.verifyPassword')}{/if}
			</button>
			<button
				type="button"
				class="settings-action"
				disabled={updatingPassword}
				onclick={() => {
					passwordCode = '';
					forgotMethod = 'totp';
					passwordStep = 'forgot';
				}}
			>
				{$t('account.forgotPassword')}
			</button>
			<button type="button" class="settings-action" onclick={resetPasswordFlow}
				>{$t('common.cancel')}</button
			>
		</div>
	{:else if passwordStep === 'forgot'}
		{#if totpStatus?.enabled}
			<div class="segmented mb-1.5" role="group">
				<button
					type="button"
					class:active={forgotMethod === 'totp'}
					onclick={() => (forgotMethod = 'totp')}
				>
					{$t('account.forgotUseTotp')}
				</button>
				<button
					type="button"
					class:active={forgotMethod === 'recovery'}
					onclick={() => (forgotMethod = 'recovery')}
				>
					{$t('account.forgotUseRecovery')}
				</button>
			</div>
			<input
				type="text"
				placeholder={forgotMethod === 'totp'
					? $t('account.totpCodePlaceholder')
					: $t('account.recoveryCodePlaceholder')}
				bind:value={passwordCode}
				inputmode={forgotMethod === 'totp' ? 'numeric' : 'text'}
				autocomplete="one-time-code"
				spellcheck="false"
				class="settings-field w-full"
				onkeydown={(e) => {
					if (e.key === 'Enter') verifyForgot();
				}}
			/>
			<div class="mt-2 flex flex-wrap gap-1.5">
				<button
					type="button"
					class="settings-action"
					disabled={updatingPassword || !passwordCode.trim()}
					onclick={verifyForgot}
				>
					{#if updatingPassword}<Spinner size={14} />{:else}{$t('account.verifyPassword')}{/if}
				</button>
				<button
					type="button"
					class="settings-action"
					onclick={() => {
						passwordCode = '';
						passwordStep = 'current';
					}}>{$t('common.cancel')}</button
				>
			</div>
		{:else}
			<p class="text-[0.75rem] text-gray-500 dark:text-gray-500">
				{$t('account.passwordForgotUnavailable')}
			</p>
			<button
				type="button"
				class="settings-action mt-2 self-start"
				onclick={() => (passwordStep = 'current')}>{$t('common.cancel')}</button
			>
		{/if}
	{:else}
		<input
			type="password"
			placeholder={$t('account.newPassword')}
			bind:value={newPassword}
			autocomplete="new-password"
			class="settings-field w-full"
			onkeydown={(e) => {
				if (e.key === 'Enter') confirmNewPassword();
			}}
		/>
		<div class="mt-2 flex flex-wrap gap-1.5">
			<button
				type="button"
				class="settings-action"
				disabled={updatingPassword || !newPassword}
				onclick={confirmNewPassword}
			>
				{#if updatingPassword}<Spinner size={14} />{:else}{$t('account.passwordConfirm')}{/if}
			</button>
			<button type="button" class="settings-action" onclick={resetPasswordFlow}
				>{$t('common.cancel')}</button
			>
		</div>
	{/if}

	<!-- Two-step sign-in -->
	<h3 class="text-xs text-gray-400 dark:text-gray-600 mt-6 mb-1">{$t('account.totpTitle')}</h3>
	{#if recoveryCodes}
		<p class="text-[0.75rem] text-gray-500 dark:text-gray-500 mb-2">
			{$t('account.totpRecoveryHint')}
		</p>
		<ul
			class="grid w-fit grid-cols-2 gap-x-6 gap-y-1 font-mono text-[0.8125rem] text-gray-800 dark:text-gray-200 select-all mb-2"
		>
			{#each recoveryCodes as recoveryCode (recoveryCode)}
				<li>{recoveryCode}</li>
			{/each}
		</ul>
		<div class="flex flex-wrap gap-1.5">
			<button type="button" class="settings-action" onclick={copyRecoveryCodes}
				>{$t('account.totpCopyCodes')}</button
			>
			<button type="button" class="settings-action" onclick={() => (recoveryCodes = null)}
				>{$t('account.totpSavedCodes')}</button
			>
		</div>
	{:else if totpStatus?.enabled}
		<p class="text-[0.75rem] text-gray-500 dark:text-gray-500">
			{$t('account.totpOn', { count: totpStatus.recovery_codes_left })}
		</p>
		{#if disablingTotp}
			<div class="mt-2 flex flex-col gap-1.5">
				<input
					type="password"
					placeholder={$t('account.currentPassword')}
					bind:value={totpPassword}
					autocomplete="current-password"
					class="settings-field w-full"
				/>
				<input
					type="text"
					placeholder={$t('account.totpCodeOrRecovery')}
					bind:value={totpCode}
					autocomplete="one-time-code"
					class="settings-field w-full"
				/>
			</div>
			<div class="mt-2 flex flex-wrap gap-1.5">
				<button
					type="button"
					disabled={totpBusy || !totpPassword || !totpCode.trim()}
					onclick={turnOffTotp}
					class="settings-action danger"
				>
					{#if totpBusy}<Spinner size={14} />{:else}{$t('account.totpDisableConfirm')}{/if}
				</button>
				<button type="button" class="settings-action" onclick={cancelTotp}
					>{$t('common.cancel')}</button
				>
			</div>
		{:else}
			<button
				type="button"
				onclick={() => (disablingTotp = true)}
				class="settings-action danger mt-2 self-start">{$t('account.totpDisable')}</button
			>
		{/if}
	{:else if totpSetup}
		<p class="text-[0.75rem] text-gray-500 dark:text-gray-500 mb-2">{$t('account.totpScan')}</p>
		<div class="w-40 h-40 p-1 bg-white rounded-md ring-1 ring-gray-200 dark:ring-white/10 mb-2">
			{@html totpSetup.qr_svg}
		</div>
		<p class="text-[0.75rem] text-gray-500 dark:text-gray-500">{$t('account.totpManual')}</p>
		<code class="font-mono text-[0.8125rem] text-gray-800 dark:text-gray-200 select-all mb-2"
			>{groupedSecret}</code
		>
		<input
			type="text"
			placeholder={$t('account.totpCodePlaceholder')}
			bind:value={totpCode}
			inputmode="numeric"
			autocomplete="one-time-code"
			maxlength="7"
			class="settings-field w-full"
		/>
		<div class="mt-2 flex flex-wrap gap-1.5">
			<button
				type="button"
				disabled={totpBusy || !totpCode.trim()}
				onclick={confirmTotp}
				class="settings-action"
			>
				{#if totpBusy}<Spinner size={14} />{:else}{$t('account.totpVerify')}{/if}
			</button>
			<button type="button" class="settings-action" onclick={cancelTotp}
				>{$t('common.cancel')}</button
			>
		</div>
	{:else if totpStatus}
		<p class="text-[0.75rem] text-gray-500 dark:text-gray-500">{$t('account.totpOffHint')}</p>
		<input
			type="password"
			placeholder={$t('account.currentPassword')}
			bind:value={totpPassword}
			autocomplete="current-password"
			class="settings-field mt-1.5 w-full"
		/>
		<button
			type="button"
			disabled={totpBusy || !totpPassword}
			onclick={startTotpSetup}
			class="settings-action mt-2 self-start"
		>
			{#if totpBusy}<Spinner size={14} />{:else}{$t('account.totpEnable')}{/if}
		</button>
	{/if}

	<!-- Save -->
	<div class="mt-auto pt-6 flex justify-end">
		<button
			type="button"
			onclick={saveDisplayName}
			disabled={savingProfile}
			class="settings-action"
		>
			{#if savingProfile}
				{$t('settings.saving')}
			{:else}
				{$t('settings.save')}
			{/if}
		</button>
	</div>
</div>
