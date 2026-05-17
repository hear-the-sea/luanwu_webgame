const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

test("guest detail page initializes attribute tooltip binding", () => {
  const calls = [];
  const detailRoot = { dataset: {} };
  const documentStub = {
    readyState: "complete",
    querySelector(selector) {
      return selector === ".guest-detail" ? detailRoot : null;
    },
    querySelectorAll() {
      return [];
    },
    addEventListener(type, callback) {
      if (type === "DOMContentLoaded") {
        callback();
      }
    },
    getElementById() {
      return null;
    },
  };
  const windowStub = {
    document: documentStub,
    initItemTooltip(options) {
      calls.push(options);
    },
    setTimeout,
    clearTimeout,
    addEventListener() {},
  };
  const context = vm.createContext({
    window: windowStub,
    document: documentStub,
    console,
    FormData: function FormData() {},
    URL,
    fetch() {},
    alert() {},
  });
  const script = fs.readFileSync(path.join(__dirname, "../guest-detail.js"), "utf8");

  vm.runInContext(script, context);

  assert.ok(
    calls.some(
      (call) =>
        call.key === "guest_detail_attributes" &&
        call.cellSelector === ".guest-attribute-tooltip-trigger" &&
        call.tooltipSelector === ".guest-attribute-tooltip-bubble" &&
        call.ignoreSelector === ".js-allocate-points-form, .add-btn" &&
        call.contentAttribute === "data-tooltip-text" &&
        call.trackPointer === false
    )
  );
});
