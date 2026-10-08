/**
 * Opening animation, after Grok App: the boot splash in app.html (the OI mark)
 * hands over to the app shell, whose regions then rise in one after another
 * while `html.cptr-intro` is set. The timeline lives in app.css; elements opt in
 * with `data-intro` / `data-intro-row`. Plays once per page load.
 */

const SPLASH_ID = 'boot-splash';
/** Keep the splash up long enough to read as intentional, not a flicker. */
const MIN_SPLASH_MS = 650;
const SPLASH_FADE_MS = 520;
/** Longest delay + duration in the app.css timeline, plus slack. */
const INTRO_MS = 2400;

/** Never leave the splash over a shell that failed to load. */
const SPLASH_FAILSAFE_MS = 12_000;

let played = false;

if (typeof window !== 'undefined') {
	setTimeout(() => void dismissSplash(), SPLASH_FAILSAFE_MS);
}

function reducedMotion() {
	return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Fade the boot splash out; resolves as it starts leaving. */
export function dismissSplash(): Promise<void> {
	const splash = document.getElementById(SPLASH_ID);
	if (!splash || splash.classList.contains('is-leaving')) return Promise.resolve();
	const wait = reducedMotion() ? 0 : Math.max(0, MIN_SPLASH_MS - performance.now());
	return new Promise((resolve) => {
		setTimeout(() => {
			splash.classList.add('is-leaving');
			setTimeout(() => splash.remove(), reducedMotion() ? 0 : SPLASH_FADE_MS);
			resolve();
		}, wait);
	});
}

/** Reveal the shell region by region. Call once it is in the DOM. */
export async function playIntro(): Promise<void> {
	if (played) return;
	played = true;
	if (reducedMotion()) {
		await dismissSplash();
		return;
	}
	const root = document.documentElement;
	// Regions start at their first keyframe, paused under the splash, so the
	// timeline begins as the splash lifts rather than behind it.
	root.classList.add('cptr-intro', 'cptr-intro-hold');
	await dismissSplash();
	root.classList.remove('cptr-intro-hold');
	setTimeout(() => root.classList.remove('cptr-intro'), INTRO_MS);
}
