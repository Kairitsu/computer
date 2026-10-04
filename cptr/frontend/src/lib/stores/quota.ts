/**
 * Subscription quota shown in the composer and sidebar footer.
 *
 * SuperGrok (the local Grok CLI login) is preferred; otherwise the user's local
 * token budget from Settings → Usage is compared with this period's usage.
 */
import { derived, get, writable } from 'svelte/store';
import { getQuota, type QuotaResponse, type SuperGrokQuota } from '$lib/apis/chat';
import { quotaBudget, type QuotaPeriod } from '$lib/stores';
import { socketStore } from '$lib/stores/socket.svelte';

const MIN_REFRESH_MS = 60_000;
const POLL_MS = 5 * 60_000;

export const quotaData = writable<QuotaResponse | null>(null);
export const quotaLoading = writable(false);

let lastFetchAt = 0;
let lastPeriod: QuotaPeriod | null = null;
let inflight: Promise<void> | null = null;
let doneTimer: ReturnType<typeof setTimeout> | null = null;
let started = false;

export function isSuperGrokQuota(value: QuotaResponse['supergrok']): value is SuperGrokQuota {
	return !!value && 'used_percent' in value;
}

/** Fetch quota; `force` also bypasses the server's SuperGrok cache. */
export async function refreshQuota(force = false): Promise<void> {
	const period = get(quotaBudget).period;
	if (!force && period === lastPeriod && Date.now() - lastFetchAt < MIN_REFRESH_MS) return;
	if (inflight) return inflight;
	quotaLoading.set(true);
	inflight = getQuota(period, force)
		.then((data) => {
			quotaData.set(data);
			lastFetchAt = Date.now();
			lastPeriod = period;
		})
		.catch(() => {})
		.finally(() => {
			inflight = null;
			quotaLoading.set(false);
		});
	return inflight;
}

/** Start polling and refresh after finished turns. Called once from the layout. */
export function startQuotaPolling(): void {
	if (started) return;
	started = true;
	void refreshQuota();
	setInterval(() => void refreshQuota(), POLL_MS);
	socketStore.on('events:chat', (data: { done?: boolean }) => {
		if (!data?.done) return;
		if (doneTimer) clearTimeout(doneTimer);
		doneTimer = setTimeout(() => {
			doneTimer = null;
			lastFetchAt = 0;
			void refreshQuota();
		}, 1500);
	});
	quotaBudget.subscribe((budget) => {
		if (lastPeriod && budget.period !== lastPeriod) void refreshQuota();
	});
}

export type QuotaSummary =
	| {
			source: 'supergrok';
			usedPercent: number;
			remainingPercent: number;
			resetsAt: number | null;
			supergrok: SuperGrokQuota;
	  }
	| {
			source: 'budget';
			usedPercent: number;
			remainingPercent: number;
			resetsAt: number;
			tokensUsed: number;
			budget: number;
			period: QuotaPeriod;
	  }
	| { source: 'none'; error: string | null; tokensUsed: number | null; period: QuotaPeriod };

export const quotaSummary = derived([quotaData, quotaBudget], ([$quota, $budget]): QuotaSummary => {
	const supergrok = $quota?.supergrok ?? null;
	if (isSuperGrokQuota(supergrok)) {
		return {
			source: 'supergrok',
			usedPercent: supergrok.used_percent,
			remainingPercent: supergrok.remaining_percent,
			resetsAt: supergrok.resets_at,
			supergrok
		};
	}
	if ($quota && $budget.tokens && $quota.local.period === $budget.period) {
		const usedPercent = Math.round(($quota.local.tokens_used / $budget.tokens) * 1000) / 10;
		return {
			source: 'budget',
			usedPercent,
			remainingPercent: Math.max(0, Math.round((100 - usedPercent) * 10) / 10),
			resetsAt: $quota.local.resets_at,
			tokensUsed: $quota.local.tokens_used,
			budget: $budget.tokens,
			period: $budget.period
		};
	}
	return {
		source: 'none',
		error: supergrok && 'error' in supergrok ? supergrok.error : null,
		tokensUsed: $quota?.local.tokens_used ?? null,
		period: $budget.period
	};
});

/** Compact token count: 128000 → "128K", 1000000 → "1M". */
export function formatTokens(value: number): string {
	if (value >= 1_000_000)
		return `${Number((value / 1_000_000).toFixed(value % 1_000_000 ? 1 : 0))}M`;
	if (value >= 1_000) return `${Number((value / 1_000).toFixed(value % 1_000 ? 1 : 0))}K`;
	return String(value);
}
