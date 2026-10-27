/** Which key inserts a line break in the session composer. Enter sends. */
export type ComposerNewline = 'shift-enter' | 'ctrl-enter' | 'alt-enter';

/** Kept so older saved preferences still parse. Enter no longer inserts a break. */
export type ComposerSendKey = 'ctrl-enter' | 'alt-enter';

export type EnterAction = 'newline' | 'send' | 'default';

type KeyEvent = {
	key: string;
	shiftKey: boolean;
	ctrlKey: boolean;
	metaKey: boolean;
	altKey: boolean;
};

/** The single modifier chord, or plain Enter. Cmd and mixed modifiers are ignored. */
function enterChord(event: KeyEvent): ComposerNewline | 'enter' | null {
	if (event.key !== 'Enter') return null;
	const pressed = [event.shiftKey, event.ctrlKey, event.metaKey, event.altKey].filter(Boolean);
	if (pressed.length > 1 || event.metaKey) return null;
	if (event.shiftKey) return 'shift-enter';
	if (event.ctrlKey) return 'ctrl-enter';
	if (event.altKey) return 'alt-enter';
	return 'enter';
}

/**
 * Decide what Enter does in the composer.
 * On a touch screen, and while a file or slash suggestion is open, plain Enter
 * stays with the editor or the suggestion. Only the selected chord inserts a break.
 */
export function enterAction(
	event: KeyEvent,
	newline: ComposerNewline,
	opts: { mobile: boolean; suggestionOpen: boolean }
): EnterAction {
	const chord = enterChord(event);
	if (!chord || opts.mobile) return 'default';
	if (opts.suggestionOpen && chord === 'enter') return 'default';
	if (chord === newline) return 'newline';
	if (chord === 'enter') return 'send';
	return 'default';
}
