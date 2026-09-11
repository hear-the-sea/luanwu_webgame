const eslintRecommended = require("@eslint/js").configs.recommended;
const globals = require("globals");

module.exports = [
  {
    ignores: [
      "node_modules/**",
      "static/css/**",
      "static/**/vendor/**",
    ],
  },
  {
    files: ["static/js/**/*.js", "tests/browser/**/*.js", "playwright.config.cjs"],
    ...eslintRecommended,
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "script",
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    rules: {
      ...eslintRecommended.rules,
      "no-unused-vars": [
        "error",
        {
          args: "none",
          caughtErrors: "none",
          ignoreRestSiblings: true,
        },
      ],
    },
  },
];
