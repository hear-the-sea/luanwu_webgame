const test = require("node:test");
const assert = require("node:assert/strict");

const partialNavCore = require("../partial_nav_core.js");

function createFakeDocument() {
  const appendedScripts = [];
  return {
    appendedScripts,
    createElement(tagName) {
      return {
        tagName: String(tagName).toUpperCase(),
        async: true,
        onload: null,
        onerror: null,
        removed: false,
        _src: "",
        set src(value) {
          this._src = value;
        },
        get src() {
          return this._src;
        },
        remove() {
          this.removed = true;
        },
      };
    },
    body: {
      appendChild(node) {
        appendedScripts.push(node);
        return node;
      },
    },
  };
}

test("runPageScripts waits for newly appended external scripts before resolving", async () => {
  const documentObj = createFakeDocument();
  const loadedScriptUrls = new Set();
  const scriptContainer = {
    querySelectorAll() {
      return [
        {
          getAttribute(name) {
            return name === "src" ? "/static/js/tasks-page.js" : null;
          },
          textContent: "",
        },
      ];
    },
  };

  let settled = false;
  const runPromise = partialNavCore
    .runPageScripts({
      scriptContainer,
      documentObj,
      currentUrl: "https://example.com/manor/tasks/",
      loadedScriptUrls,
      executeInlineScript() {},
    })
    .then(() => {
      settled = true;
    });

  await Promise.resolve();

  assert.equal(documentObj.appendedScripts.length, 1);
  assert.equal(settled, false);
  assert.equal(documentObj.appendedScripts[0].src, "https://example.com/static/js/tasks-page.js");

  documentObj.appendedScripts[0].onload();
  await runPromise;

  assert.equal(settled, true);
  assert.ok(loadedScriptUrls.has("https://example.com/static/js/tasks-page.js"));
});

test("runPageScripts skips already loaded scripts and still runs inline code", async () => {
  const documentObj = createFakeDocument();
  const loadedScriptUrls = new Set(["https://example.com/static/js/tasks-page.js"]);
  const inlineCalls = [];
  const scriptContainer = {
    querySelectorAll() {
      return [
        {
          getAttribute(name) {
            return name === "src" ? "/static/js/tasks-page.js" : null;
          },
          textContent: "",
        },
        {
          getAttribute() {
            return null;
          },
          textContent: "window.__inlineExecuted = true;",
        },
      ];
    },
  };

  await partialNavCore.runPageScripts({
    scriptContainer,
    documentObj,
    currentUrl: "https://example.com/manor/tasks/",
    loadedScriptUrls,
    executeInlineScript(code) {
      inlineCalls.push(code);
    },
  });

  assert.equal(documentObj.appendedScripts.length, 0);
  assert.deepEqual(inlineCalls, ["window.__inlineExecuted = true;"]);
});

test("runPageScripts aborts a pending external script and skips later inline code", async () => {
  const documentObj = createFakeDocument();
  const controller = new AbortController();
  const inlineCalls = [];
  const scriptContainer = {
    querySelectorAll() {
      return [
        {
          getAttribute(name) {
            return name === "src" ? "/static/js/slow-page.js" : null;
          },
          textContent: "",
        },
        {
          getAttribute() {
            return null;
          },
          textContent: "window.__staleInlineExecuted = true;",
        },
      ];
    },
  };

  const runPromise = partialNavCore.runPageScripts({
    scriptContainer,
    documentObj,
    currentUrl: "https://example.com/manor/tasks/",
    loadedScriptUrls: new Set(),
    executeInlineScript(code) {
      inlineCalls.push(code);
    },
    signal: controller.signal,
  });

  await Promise.resolve();
  assert.equal(documentObj.appendedScripts.length, 1);

  controller.abort();
  if (documentObj.appendedScripts[0].onload) {
    documentObj.appendedScripts[0].onload();
  }

  await assert.rejects(runPromise, (error) => error && error.name === "AbortError");
  assert.equal(documentObj.appendedScripts[0].removed, true);
  assert.deepEqual(inlineCalls, []);
});
