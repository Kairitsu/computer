import type { ReasoningEffort } from '$lib/apis/chat';

/** Higher is more thinking. `enabled` is an unmarked model default, not a strength. */
const EFFORT_RANK: Record<string, number> = {
	disabled: -2,
	none: -2,
	enabled: -1,
	minimal: 0,
	low: 1,
	medium: 2,
	high: 3,
	xhigh: 4,
	max: 5
};

export interface EffortOption {
	value: string;
}

export interface ModelCaps {
	supports_reasoning_effort?: boolean;
	reasoning_efforts?: EffortOption[];
	context_windows?: number[];
	context_window?: number;
}

/** Strongest effort the model lists. Unknown names stay below the known strengths. */
export function highestEffort(model: ModelCaps | undefined): ReasoningEffort | null {
	const efforts = model?.reasoning_efforts ?? [];
	if (!efforts.length || model?.supports_reasoning_effort === false) return null;
	let best: string | null = null;
	let bestRank = -Infinity;
	efforts.forEach((option, index) => {
		const rank =
			option.value in EFFORT_RANK ? EFFORT_RANK[option.value] : -0.5 + index * 0.001;
		if (rank > bestRank) {
			bestRank = rank;
			best = option.value;
		}
	});
	return best as ReasoningEffort | null;
}

/** Longest window the model lists, or the single window it publishes. */
export function longestContext(model: ModelCaps | undefined): number | null {
	const windows = (model?.context_windows ?? []).filter((value) => value > 0);
	if (windows.length) return Math.max(...windows);
	return model?.context_window && model.context_window > 0 ? model.context_window : null;
}
