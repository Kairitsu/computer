<script lang="ts">
	/** Settings → Usage → Switch account: saved Grok CLI logins, after Grok App. */
	import type { GrokSavedAccount } from '$lib/apis/grok';
	import { t } from '$lib/i18n';
	import Icon from '../Icon.svelte';
	import Modal from '../Modal.svelte';

	interface Props {
		accounts: GrokSavedAccount[];
		signedIn: boolean;
		busy: boolean;
		onswitch: (id: string) => void;
		onremove: (id: string) => void;
		onsave: () => void;
		onadd: () => void;
		onclose: () => void;
	}

	let { accounts, signedIn, busy, onswitch, onremove, onsave, onadd, onclose }: Props = $props();

	function initials(account: GrokSavedAccount) {
		const parts = (account.label || account.email || '?').trim().split(/\s+/).filter(Boolean);
		if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
		return (parts[0] ?? '?').slice(0, 2).toUpperCase();
	}
</script>

<Modal {onclose} class="mx-4 flex w-full max-w-md flex-col p-5">
	<h3 class="text-sm font-semibold" style="color: var(--app-fg);">{$t('grokAccount.accounts')}</h3>
	<p class="mt-1.5 text-xs leading-relaxed" style="color: var(--app-fg-muted);">
		{$t('grokAccount.accountsHint')}
	</p>

	{#if accounts.length === 0}
		<div class="empty mt-4 rounded-xl px-3 py-6 text-center text-xs">
			{$t('grokAccount.accountsEmpty')}
		</div>
	{:else}
		<ul class="mt-4 flex max-h-72 flex-col gap-1.5 overflow-y-auto">
			{#each accounts as account (account.id)}
				<li
					class="row flex items-center gap-3 rounded-xl px-3 py-2.5"
					class:is-active={account.active}
				>
					<span class="avatar" aria-hidden="true">{initials(account)}</span>
					<div class="min-w-0 flex-1">
						<div class="flex items-center gap-2">
							<span class="truncate text-[0.8125rem] font-medium" style="color: var(--app-fg);"
								>{account.label}</span
							>
							{#if account.active}
								<span class="badge">{$t('grokAccount.current')}</span>
							{/if}
						</div>
						{#if account.email && account.email !== account.label}
							<div class="truncate text-[0.6875rem]" style="color: var(--app-fg-subtle);">
								{account.email}
							</div>
						{/if}
					</div>
					<div class="flex shrink-0 items-center gap-1.5">
						{#if !account.active}
							<button
								type="button"
								class="btn btn-solid"
								disabled={busy}
								onclick={() => onswitch(account.id)}>{$t('grokAccount.switch')}</button
							>
						{/if}
						<button
							type="button"
							class="btn btn-ghost btn-danger"
							disabled={busy}
							aria-label={$t('grokAccount.remove')}
							onclick={() => onremove(account.id)}
						>
							<Icon name="trash" size={12} />
						</button>
					</div>
				</li>
			{/each}
		</ul>
	{/if}

	<div class="mt-5 flex flex-wrap items-center justify-end gap-2">
		{#if signedIn}
			<button type="button" class="btn btn-ghost" disabled={busy} onclick={onsave}
				>{$t('grokAccount.saveCurrent')}</button
			>
		{/if}
		<button type="button" class="btn btn-ghost" onclick={onclose}>{$t('common.close')}</button>
		<button type="button" class="btn btn-solid" disabled={busy} onclick={onadd}>
			<Icon name="plus" size={12} />
			{$t('grokAccount.addAccount')}
		</button>
	</div>
</Modal>

<style>
	.empty {
		color: var(--app-fg-muted);
		background: color-mix(in oklab, var(--app-fg) 4%, transparent);
	}

	.row {
		border: 1px solid color-mix(in oklab, var(--app-fg) 8%, transparent);
	}

	.row.is-active {
		border-color: color-mix(in oklab, var(--app-accent) 45%, transparent);
		background: color-mix(in oklab, var(--app-accent) 6%, transparent);
	}

	.avatar {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 2rem;
		height: 2rem;
		flex-shrink: 0;
		border-radius: 999px;
		font-size: 0.6875rem;
		font-weight: 700;
		color: var(--app-accent);
		background: color-mix(in oklab, var(--app-accent) 12%, transparent);
	}

	.badge {
		flex-shrink: 0;
		padding: 0 0.4375rem;
		border-radius: 999px;
		font-size: 0.625rem;
		line-height: 1.125rem;
		color: var(--app-fg-muted);
		border: 1px solid color-mix(in oklab, var(--app-fg) 12%, transparent);
	}

	.btn {
		display: inline-flex;
		align-items: center;
		gap: 0.3125rem;
		height: 1.75rem;
		padding: 0 0.75rem;
		border-radius: 0.5rem;
		font-size: 0.75rem;
		font-weight: 500;
		transition:
			background 0.12s ease,
			opacity 0.12s ease;
	}

	.btn:disabled {
		opacity: 0.45;
		pointer-events: none;
	}

	.btn-solid {
		background: var(--app-accent);
		color: var(--app-accent-fg);
	}

	.btn-solid:hover {
		opacity: 0.88;
	}

	.btn-ghost {
		color: var(--app-fg);
		border: 1px solid color-mix(in oklab, var(--app-fg) 12%, transparent);
	}

	.btn-ghost:hover {
		background: var(--app-hover);
	}

	.btn-danger {
		padding: 0 0.5rem;
		color: #dc2626;
	}
</style>
