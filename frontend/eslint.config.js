import js from '@eslint/js';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist', 'node_modules'] },
  js.configs.recommended,
  ...tseslint.configs.strict,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: { globals: globals.browser },
    plugins: { react, 'react-hooks': reactHooks },
    settings: { react: { version: 'detect' } },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // CLAUDE.md rule 12: model, Copilot and buyer text is rendered as plain text only.
      'react/no-danger': 'error',
      'react/no-danger-with-children': 'error',
      'no-restricted-syntax': [
        'error',
        { selector: "MemberExpression[property.name='innerHTML']", message: 'Render text as plain text (CLAUDE.md rule 12).' },
        { selector: "MemberExpression[property.name='outerHTML']", message: 'Render text as plain text (CLAUDE.md rule 12).' },
        { selector: "CallExpression[callee.property.name='insertAdjacentHTML']", message: 'Render text as plain text (CLAUDE.md rule 12).' },
      ],
      // Every API call goes through src/api/ (CLAUDE.md conventions).
      'no-restricted-globals': [
        'error',
        { name: 'fetch', message: 'Call the backend through src/api/ only.' },
      ],
    },
  },
  {
    files: ['src/api/**/*.ts'],
    rules: { 'no-restricted-globals': 'off' },
  },
);
