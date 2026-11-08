import { messageAlreadyHasPlan, nextPlanText } from './grokPlan';

export interface ActivityGroup {
	type: 'activity_group';
	entries: any[];
	calls: any[];
	reasoning: any[];
	outputs: Map<string, any>;
}

export interface MessageItem {
	type: 'message_item';
	item: any;
}

export interface PlanItem {
	type: 'plan_item';
	text: string;
}

export interface ArtifactItem {
	type: 'artifact_item';
	item: any;
}

export interface ImageItem {
	type: 'image_item';
	item: any;
}

export interface FileItem {
	type: 'file_item';
	item: any;
	index: number;
}

export type DisplayItem =
	| ActivityGroup
	| MessageItem
	| PlanItem
	| ArtifactItem
	| ImageItem
	| FileItem;

export function messageItemText(item: any): string {
	return (item.content || []).map((part: any) => part.text || '').join('');
}

/**
 * Group consecutive reasoning and tool calls, and lift a Grok session plan out of
 * the Write that saved it. A later Write of the same file replaces the earlier copy.
 * The tool call itself stays in the activity group.
 */
export function buildAssistantDisplayItems(output: any[] | null | undefined): DisplayItem[] {
	if (!output?.length) return [];

	const items: DisplayItem[] = [];
	let currentGroup: ActivityGroup | null = null;

	const outputMap = new Map<string, any>();
	for (const item of output) {
		if (item.type === 'function_call_output' && item.call_id) {
			outputMap.set(item.call_id, item);
		}
	}

	const ensureGroup = () => {
		if (!currentGroup) {
			currentGroup = {
				type: 'activity_group',
				entries: [],
				calls: [],
				reasoning: [],
				outputs: outputMap
			};
		}
	};

	const flushGroup = () => {
		if (currentGroup && (currentGroup.calls.length > 0 || currentGroup.reasoning.length > 0)) {
			items.push(currentGroup);
			currentGroup = null;
		}
	};

	let planText: string | null = null;

	const placePlan = (text: string) => {
		const existing = items.findIndex((entry) => entry.type === 'plan_item');
		if (existing >= 0) items.splice(existing, 1);
		items.push({ type: 'plan_item', text });
	};

	for (const [index, item] of output.entries()) {
		if (item.type === 'function_call') {
			// A plan approval is the card under the composer, not a question in the transcript.
			const planAsk = item.name === 'ask_user' && item.arguments?.kind === 'plan';
			if (!planAsk && (item.name !== 'ask_user' || item.status !== 'pending')) {
				ensureGroup();
				currentGroup!.entries.push(item);
				currentGroup!.calls.push(item);
			}
			const next = nextPlanText(planText, item);
			if (next) {
				planText = next;
				flushGroup();
				placePlan(planText);
			}
		} else if (item.type === 'reasoning') {
			ensureGroup();
			currentGroup!.entries.push(item);
			currentGroup!.reasoning.push(item);
		} else if (item.type === 'message') {
			// Empty message shells sit between reasoning and tools. Only visible text breaks a run.
			if (messageItemText(item).trim()) {
				flushGroup();
				items.push({ type: 'message_item', item });
			}
		} else if (item.type === 'artifact') {
			flushGroup();
			items.push({ type: 'artifact_item', item });
		} else if (item.type === 'image') {
			flushGroup();
			items.push({ type: 'image_item', item });
		} else if (item.type === 'file') {
			flushGroup();
			items.push({ type: 'file_item', item, index });
		}
	}
	flushGroup();

	const planIndex = items.findIndex((entry) => entry.type === 'plan_item');
	if (planIndex >= 0) {
		const plan = items[planIndex] as PlanItem;
		const spoken = items
			.filter((entry): entry is MessageItem => entry.type === 'message_item')
			.map((entry) => messageItemText(entry.item))
			.join('\n');
		if (messageAlreadyHasPlan(spoken, plan.text)) {
			items.splice(planIndex, 1);
		}
	}

	return items;
}
