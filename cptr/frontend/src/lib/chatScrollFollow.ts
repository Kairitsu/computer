/** Distance from the bottom that still counts as the reader sitting at the bottom. */
export const BOTTOM_SLACK_PX = 8;

export interface ScrollFollow {
	/** New transcript growth may move the viewport only while this is true. */
	pinned: boolean;
	lastScrollTop: number;
}

export interface ScrollSample {
	scrollTop: number;
	scrollHeight: number;
	clientHeight: number;
	/** This sample came from our own scrollTop write. */
	programmatic: boolean;
	/** Wheel, touch, keyboard, or a drag the reader started. */
	userInitiated: boolean;
}

/**
 * Decide whether the transcript keeps following the latest text.
 * A programmatic write never pins or unpins. Only the reader's own move does.
 * Moving up releases the follow. Moving down pins again only at the bottom.
 */
export function reduceScroll(state: ScrollFollow, sample: ScrollSample): ScrollFollow {
	if (sample.programmatic || !sample.userInitiated) {
		return { pinned: state.pinned, lastScrollTop: sample.scrollTop };
	}

	const movedUp = sample.scrollTop < state.lastScrollTop - 1;
	const movedDown = sample.scrollTop > state.lastScrollTop + 1;
	const distanceFromBottom = sample.scrollHeight - sample.scrollTop - sample.clientHeight;

	if (movedUp) {
		return { pinned: false, lastScrollTop: sample.scrollTop };
	}
	if (movedDown && distanceFromBottom <= BOTTOM_SLACK_PX) {
		return { pinned: true, lastScrollTop: sample.scrollTop };
	}
	return { pinned: state.pinned, lastScrollTop: sample.scrollTop };
}

/** Wheel delta that asks the page to move upward. Zoom gestures are not scrolls. */
export function isUpwardWheel(deltaY: number): boolean {
	return deltaY < 0;
}

/** Finger moved down, so the page content moves toward earlier messages. */
export function isUpwardTouch(previousY: number, nextY: number): boolean {
	return nextY > previousY + 2;
}

const UP_KEYS = new Set(['ArrowUp', 'PageUp', 'Home']);
const SCROLL_KEYS = new Set(['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' ']);

export function isTranscriptScrollKey(key: string): boolean {
	return SCROLL_KEYS.has(key);
}

export function isUpwardKey(key: string, shiftKey: boolean): boolean {
	if (UP_KEYS.has(key)) return true;
	return key === ' ' && shiftKey;
}
