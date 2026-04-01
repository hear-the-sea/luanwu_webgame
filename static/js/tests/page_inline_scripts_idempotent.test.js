const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function extractInlineScript(templatePath) {
  const content = fs.readFileSync(templatePath, "utf8");
  const match = content.match(/{% block extra_scripts %}\s*<script>([\s\S]*?)<\/script>\s*{% endblock %}/);
  assert.ok(match, `expected inline extra_scripts block in ${templatePath}`);
  return match[1].replace(/{{[^}]+}}/g, "0").replace(/{%[^%]+%}/g, "");
}

function createMockElement() {
  return {
    action: "",
    style: {},
    textContent: "",
    value: "",
    max: "",
    hidden: false,
    disabled: false,
    required: false,
    dataset: {},
    querySelector() {
      return { textContent: "" };
    },
    querySelectorAll() {
      return [];
    },
    addEventListener() {},
    setAttribute() {},
    removeAttribute() {},
  };
}

function createDocumentContext() {
  const elementMap = new Map();

  return {
    window: null,
    document: {
      getElementById(id) {
        if (!elementMap.has(id)) {
          elementMap.set(id, createMockElement());
        }
        return elementMap.get(id);
      },
      querySelectorAll() {
        return [];
      },
    },
    console,
    gameDialog: {
      async danger() {
        return true;
      },
    },
    parseInt,
    Array,
  };
}

function assertScriptCanExecuteTwice(scriptSource, options) {
  const { filename } = options;
  const context = createDocumentContext();
  context.window = context;
  vm.createContext(context);
  vm.runInContext(scriptSource, context, { filename });
  assert.doesNotThrow(() => vm.runInContext(scriptSource, context, { filename }));
}

test("guild detail inline script stays idempotent across partial navigation re-runs", () => {
  const scriptSource = extractInlineScript(
    path.resolve(__dirname, "../../..", "guilds/templates/guilds/detail.html")
  );

  assertScriptCanExecuteTwice(scriptSource, { filename: "guilds/templates/guilds/detail.html" });
});

test("guild warehouse inline script stays idempotent across partial navigation re-runs", () => {
  const scriptSource = extractInlineScript(
    path.resolve(__dirname, "../../..", "guilds/templates/guilds/warehouse.html")
  );

  assertScriptCanExecuteTwice(scriptSource, { filename: "guilds/templates/guilds/warehouse.html" });
});
