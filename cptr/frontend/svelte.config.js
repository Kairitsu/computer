import adapter from '@sveltejs/adapter-static';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	compilerOptions: {
		// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
		runes: ({ filename }) => (filename.split(/[/\\]/).includes('node_modules') ? undefined : true)
	},
	kit: {
		adapter: adapter({
			// In-app updates build into a staging directory, then swap it in.
			pages: process.env.CPTR_FRONTEND_OUT || 'build',
			assets: process.env.CPTR_FRONTEND_OUT || 'build',
			fallback: 'index.html'
		}),
		paths: {
			relative: false
		},
		serviceWorker: {
			register: false
		}
	}
};

export default config;
