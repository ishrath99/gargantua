import typography from '@tailwindcss/typography';
import type { Config } from 'tailwindcss';

/**
 * Tailwind config.  Light defaults; shadcn/ui primitives drop in via
 * components rather than design tokens, so we keep the theme minimal.
 *
 * The ``@tailwindcss/typography`` plugin powers the ``prose`` classes
 * used on Markdown-rendering surfaces (agent-template detail, audit
 * diff modal).
 */
const config: Config = {
  darkMode: 'media',
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [typography],
};

export default config;
