/** Text Grok wrote into its session plan file, shown as a normal reply. */

export function isGrokSessionPlanPath(path: string): boolean {
	const normalized = path.replaceAll('\\', '/');
	return /\/\.grok\/sessions\/.+\/plan\.md$/.test(normalized);
}

function planInput(item: {
	type?: string;
	arguments?: { input?: unknown };
}): Record<string, unknown> | null {
	if (item?.type !== 'function_call') return null;
	const input = item.arguments?.input;
	if (!input || typeof input !== 'object') return null;
	return input as Record<string, unknown>;
}

/**
 * The plan text after this tool call.
 * A Write, or a SearchReplace that creates the file, replaces the whole plan.
 * A later SearchReplace of the same file updates that text.
 * Returns null when the call is not an edit of the session plan.
 */
export function nextPlanText(
	current: string | null,
	item: { type?: string; arguments?: { input?: unknown } }
): string | null {
	const raw = planInput(item);
	if (!raw) return null;
	const path = raw.file_path ?? raw.path;
	if (typeof path !== 'string' || !isGrokSessionPlanPath(path)) return null;
	if (raw.variant === 'Write' && typeof raw.content === 'string' && raw.content.trim()) {
		return raw.content;
	}
	if (
		raw.variant !== 'SearchReplace' ||
		typeof raw.new_string !== 'string' ||
		!raw.new_string.trim()
	) {
		return null;
	}
	const old = typeof raw.old_string === 'string' ? raw.old_string : '';
	if (!old) return raw.new_string;
	if (!current || !current.includes(old)) return null;
	return raw.replace_all === true
		? current.split(old).join(raw.new_string)
		: current.replace(old, raw.new_string);
}

/** Full markdown from one Write or whole-file SearchReplace of the session plan. */
export function planTextFromToolCall(item: {
	type?: string;
	arguments?: { input?: unknown };
}): string | null {
	return nextPlanText(null, item);
}

/** The user chose 修改计划, so this finished reply should not ask for approval again. */
export function planAskWasRevise(output: any[] | null | undefined): boolean {
	if (!output?.length) return false;
	let revised = false;
	for (const item of output) {
		if (item?.type !== 'function_call' || item.name !== 'ask_user') continue;
		if (item.arguments?.kind !== 'plan') continue;
		if (item.status === 'pending') return false;
		const paired = output.find(
			(entry) => entry?.type === 'function_call_output' && entry.call_id === item.call_id
		);
		let answers: unknown;
		try {
			answers = JSON.parse(paired?.output || '{}')?.answers?.plan?.answers;
		} catch {
			answers = undefined;
		}
		revised = Array.isArray(answers) && answers.includes('Keep planning');
	}
	return revised;
}

/** True when the spoken reply already contains this plan, so it is not shown twice. */
export function messageAlreadyHasPlan(messageText: string, plan: string): boolean {
	const norm = (value: string) => value.replace(/\s+/g, ' ').trim();
	const planNorm = norm(plan);
	if (!planNorm) return true;
	return norm(messageText).includes(planNorm);
}
