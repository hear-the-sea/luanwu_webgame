const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.resolve(__dirname, "..", "production-cancel.js"), "utf8");

function createHarness({ confirmed }) {
  const listeners = [];
  const calls = {
    danger: [],
    preventDefault: 0,
    submit: 0,
  };
  const form = {
    dataset: {
      confirmTitle: "取消锻造",
      confirmMessage: "材料不会返还",
      confirmOkText: "确认取消",
    },
    submit() {
      calls.submit += 1;
    },
  };
  const document = {
    addEventListener(type, handler) {
      if (type === "submit") {
        listeners.push(handler);
      }
    },
  };
  const windowObject = {
    gameDialog: {
      danger(message, options) {
        calls.danger.push({ message, options });
        return Promise.resolve(confirmed);
      },
    },
    confirm() {
      throw new Error("native confirm should not be used");
    },
  };
  const context = { window: windowObject, document, console };

  function runScript() {
    vm.runInNewContext(source, context, { filename: "production-cancel.js" });
  }

  async function submit() {
    assert.equal(listeners.length, 1);
    await listeners[0]({
      target: {
        closest(selector) {
          return selector === ".js-production-cancel-form" ? form : null;
        },
      },
      preventDefault() {
        calls.preventDefault += 1;
      },
    });
  }

  return { calls, form, listeners, runScript, submit };
}

test("production cancel submits after an explicit irreversible-action confirmation", async () => {
  const harness = createHarness({ confirmed: true });
  harness.runScript();

  await harness.submit();

  assert.equal(harness.calls.preventDefault, 1);
  assert.equal(harness.calls.submit, 1);
  assert.equal(harness.calls.danger.length, 1);
  assert.equal(harness.calls.danger[0].message, "材料不会返还");
  assert.equal(harness.calls.danger[0].options.title, "取消锻造");
  assert.equal(harness.calls.danger[0].options.okText, "确认取消");
  assert.equal(harness.calls.danger[0].options.cancelText, "返回");
  assert.equal(harness.form.dataset.confirmed, "true");
});

test("production cancel leaves the task running when confirmation is declined", async () => {
  const harness = createHarness({ confirmed: false });
  harness.runScript();

  await harness.submit();

  assert.equal(harness.calls.preventDefault, 1);
  assert.equal(harness.calls.submit, 0);
  assert.equal(harness.form.dataset.confirmed, undefined);
  assert.equal(harness.form.dataset.confirming, undefined);
});

test("production cancel installs one delegated handler across partial navigation runs", () => {
  const harness = createHarness({ confirmed: true });

  harness.runScript();
  harness.runScript();

  assert.equal(harness.listeners.length, 1);
});
