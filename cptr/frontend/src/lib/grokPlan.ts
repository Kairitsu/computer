/** Text Grok wrote into its session plan file, shown as a normal reply. */

export function isGrokSessionPlanPath(path: string): boolean {
	const normalized = path.replaceAll('\\', '/');
	return /\/\.grok\/sessions\/.+\/plan\.md$/.test(normalized);
}

/** Full markdown from a Write of `~/.grok/sessions/.../plan.md`. Other files stay in the tool call. */
export function planTextFromToolCall(item: {
	type?: string;
	arguments?: { input?: unknown };
}): string | null {
	if (item?.type !== 'function_call') return null;
	const input = item.arguments?.input;
	if (!input || typeof input !== 'object') return null;
	const raw = input as Record<string, unknown>;
	if (raw.variant !== 'Write') return null;
	const path = raw.file_path ?? raw.path;
	if (typeof path !== 'string' || !isGrokSessionPlanPath(path)) return null;
	if (typeof raw.content !== 'string' || !raw.content.trim()) return null;
	return raw.content;
}

/** True when the spoken reply already contains this plan, so it is not shown twice. */
export function messageAlreadyHasPlan(messageText: string, plan: string): boolean {
	const norm = (value: string) => value.replace(/\s+/g, ' ').trim();
	const planNorm = norm(plan);
	if (!planNorm) return true;
	return norm(messageText).includes(planNorm);
}
