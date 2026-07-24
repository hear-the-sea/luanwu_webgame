const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const css = fs.readFileSync(path.join(__dirname, "..", "..", "css", "jail.css"), "utf8");

test("mobile jail workspace reserves a control rail for the chat button", () => {
  assert.match(css, /@media \(max-width: 560px\)[\s\S]*\.jail-prisoner-content\s*\{[^}]*padding-right:\s*74px;/);
});

test("jail result dialogs preserve the story and numeric summary line break", () => {
  assert.match(css, /\.game-dialog-message\s*\{[^}]*white-space:\s*pre-line;[^}]*text-align:\s*left;/);
  assert.match(css, /\.game-dialog-message\s*\{[^}]*max-height:\s*55vh;[^}]*overflow-y:\s*auto;/);
});
