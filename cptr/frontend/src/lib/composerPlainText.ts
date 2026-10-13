import { Fragment, Slice, type Schema } from '@tiptap/pm/model';

/** TipTap mention markdown, and the file link stored on a sent message. */
const TOKEN_RE = /\[@\s+id="([^"]*)"\s+label="([^"]*)"\]|\[([^\]]*)\]\(file:\/\/([^)]*)\)/g;

type Token = { type: 'text'; text: string } | { type: 'mention'; id: string; label: string };

/** Turn both mention spellings into the file-link form the editor serializes. */
export function canonicalComposerText(text: string): string {
	return text.replace(
		/\[@\s+id="([^"]*)"\s+label="([^"]*)"\]/g,
		(_match, id: string, label: string) => `[${label}](file://${id})`
	);
}

function tokenizeLine(line: string): Token[] {
	const tokens: Token[] = [];
	let last = 0;
	for (const match of line.matchAll(TOKEN_RE)) {
		const index = match.index ?? 0;
		if (index > last) tokens.push({ type: 'text', text: line.slice(last, index) });
		if (match[0].startsWith('[@')) {
			tokens.push({ type: 'mention', id: match[1], label: match[2] });
		} else {
			tokens.push({ type: 'mention', id: match[4], label: match[3] });
		}
		last = index + match[0].length;
	}
	if (last < line.length) tokens.push({ type: 'text', text: line.slice(last) });
	return tokens;
}

function inlineContent(line: string) {
	return tokenizeLine(line).map((token) =>
		token.type === 'text'
			? { type: 'text', text: token.text }
			: {
					type: 'mention',
					attrs: { id: token.id, label: token.label, mentionSuggestionChar: '@' }
				}
	);
}

/** One paragraph per line, including blank lines. Mentions become chips. */
export function plainTextToDoc(text: string) {
	return {
		type: 'doc',
		content: text.split('\n').map((line) => {
			const content = inlineContent(line);
			return content.length ? { type: 'paragraph', content } : { type: 'paragraph' };
		})
	};
}

/**
 * Clipboard slice that keeps every newline, including blank lines.
 * Open ends let the first and last line merge with the paragraph around the cursor.
 */
export function plainTextSlice(schema: Schema, text: string): Slice {
	const normalized = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
	const paragraphs = normalized.split('\n').map((line) => {
		const content = tokenizeLine(line).flatMap((token) => {
			if (token.type === 'text') return token.text ? [schema.text(token.text)] : [];
			const mention = schema.nodes.mention;
			if (!mention) return token.id || token.label ? [schema.text(`[${token.label}](file://${token.id})`)] : [];
			return [mention.create({ id: token.id, label: token.label, mentionSuggestionChar: '@' })];
		});
		return schema.nodes.paragraph.create(null, content);
	});
	return new Slice(Fragment.from(paragraphs), 1, 1);
}
