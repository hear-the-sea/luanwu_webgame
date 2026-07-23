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

test("jail page uses the shared theme and a responsive operation grid", () => {
  assert.match(css, /\.jail-operation-grid\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:/);
  assert.match(css, /\.jail-prisoner-panel\s*\{[^}]*background:\s*var\(--bg-panel\)/);
  assert.doesNotMatch(css, /--jail-paper|--jail-ink|--jail-seal/);
});

test("jail cells have visible boundaries and separated title bars", () => {
  assert.match(css, /\.jail-cell\s*\{[^}]*border:\s*2px\s+solid\s+var\(--border-primary\);/);
  assert.match(css, /\.jail-cell-header\s*\{[^}]*border-bottom:\s*1px\s+solid\s+var\(--border-light\);/);
  assert.match(css, /\.jail-prisoner-list\s*\{[^}]*gap:\s*24px;/);
});
