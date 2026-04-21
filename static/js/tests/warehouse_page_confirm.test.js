const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function readScript(relativePath) {
  return fs.readFileSync(path.resolve(__dirname, "..", relativePath), "utf8");
}

function createWarehouseTestEnv({
  confirmText = "确认使用卷轴？",
  confirmResult = true,
  useGameConfirm = true,
  useGameDialogConfirm = true,
} = {}) {
  const source = readScript("warehouse-page.js");
  const listeners = new Map();
  const calls = {
    fetch: [],
    gameConfirm: [],
    dialogConfirm: [],
    success: [],
    error: [],
    reload: 0,
    preventDefault: 0,
  };

  const submitButton = {
    textContent: "使用",
    disabled: false,
  };

  const form = {
    action: "/warehouse/use/1/",
    dataset: {},
    querySelector(selector) {
      if (selector === 'button[type="submit"]') {
        return submitButton;
      }
      return null;
    },
    addEventListener(type, handler) {
      listeners.set(`form:${type}`, handler);
    },
  };

  if (confirmText) {
    form.dataset.confirmText = confirmText;
    form.dataset.confirmTitle = "使用确认";
    form.dataset.confirmOkText = "确认使用";
  }

  const document = {
    documentElement: { scrollTop: 0 },
    addEventListener(type, handler) {
      listeners.set(type, handler);
    },
    querySelector(selector) {
      if (selector === ".tw-warehouse-card") {
        return { dataset: {} };
      }
      return null;
    },
    querySelectorAll(selector) {
      if (selector === ".tw-warehouse-actions form, .tw-action-return-form") {
        return [form];
      }
      return [];
    },
    getElementById() {
      return null;
    },
  };

  const sessionStorage = {
    _data: new Map(),
    getItem(key) {
      return this._data.has(key) ? this._data.get(key) : null;
    },
    setItem(key, value) {
      this._data.set(key, String(value));
    },
    removeItem(key) {
      this._data.delete(key);
    },
  };

  const windowObject = {
    WarehousePageCore: {
      formatSoulFusionRequirementHint() {
        return "";
      },
      buildWarehouseFilterState() {
        return {};
      },
      shouldGuestMatchWarehouseFilter() {
        return true;
      },
    },
    gameDialog: {
      success(message) {
        calls.success.push(message);
        return Promise.resolve();
      },
      error(message) {
        calls.error.push(message);
        return Promise.resolve();
      },
    },
    location: {
      reload() {
        calls.reload += 1;
      },
    },
    scrollY: 0,
    scrollTo() {},
  };

  if (useGameConfirm) {
    windowObject.gameConfirm = (message, options) => {
      calls.gameConfirm.push({ message, options });
      return Promise.resolve(confirmResult);
    };
  }

  if (useGameDialogConfirm) {
    windowObject.gameDialog.confirm = (message, options) => {
      calls.dialogConfirm.push({ message, options });
      return Promise.resolve(confirmResult);
    };
  }

  const context = {
    window: windowObject,
    document,
    sessionStorage,
    FormData: function FakeFormData(target) {
      this.target = target;
    },
    fetch(url, options) {
      calls.fetch.push({ url, options });
      return Promise.resolve({
        json: () => Promise.resolve({ success: true, message: "操作成功" }),
      });
    },
    alert() {},
    console,
    setTimeout,
    clearTimeout,
  };

  function runWarehouseScript() {
    vm.runInNewContext(source, context, { filename: "warehouse-page.js" });
    const onReady = listeners.get("DOMContentLoaded");
    assert.equal(typeof onReady, "function");
    onReady();
  }

  async function dispatchSubmit() {
    const handler = listeners.get("form:submit");
    assert.equal(typeof handler, "function");
    await handler({
      preventDefault() {
        calls.preventDefault += 1;
      },
    });
  }

  return {
    calls,
    dispatchSubmit,
    form,
    runWarehouseScript,
    submitButton,
  };
}

test("warehouse page confirms configured forms before submitting", async () => {
  const env = createWarehouseTestEnv({ confirmResult: true });

  env.runWarehouseScript();
  await env.dispatchSubmit();

  assert.equal(env.calls.preventDefault, 1);
  assert.equal(env.calls.gameConfirm.length, 1);
  assert.equal(env.calls.dialogConfirm.length, 0);
  assert.equal(env.calls.fetch.length, 1);
  assert.equal(env.calls.reload, 1);
  assert.equal(env.submitButton.disabled, true);
  assert.equal(env.submitButton.textContent, "处理中...");
});

test("warehouse page aborts submit when confirmation is cancelled", async () => {
  const env = createWarehouseTestEnv({ confirmResult: false });

  env.runWarehouseScript();
  await env.dispatchSubmit();

  assert.equal(env.calls.preventDefault, 1);
  assert.equal(env.calls.gameConfirm.length, 1);
  assert.equal(env.calls.fetch.length, 0);
  assert.equal(env.calls.reload, 0);
  assert.equal(env.submitButton.disabled, false);
  assert.equal(env.submitButton.textContent, "使用");
});

test("warehouse page submits immediately when no confirm metadata is configured", async () => {
  const env = createWarehouseTestEnv({ confirmText: "" });

  env.runWarehouseScript();
  await env.dispatchSubmit();

  assert.equal(env.calls.gameConfirm.length, 0);
  assert.equal(env.calls.dialogConfirm.length, 0);
  assert.equal(env.calls.fetch.length, 1);
  assert.equal(env.calls.reload, 1);
});

test("warehouse page falls back to gameDialog.confirm when gameConfirm is unavailable", async () => {
  const env = createWarehouseTestEnv({
    confirmResult: true,
    useGameConfirm: false,
    useGameDialogConfirm: true,
  });

  env.runWarehouseScript();
  await env.dispatchSubmit();

  assert.equal(env.calls.gameConfirm.length, 0);
  assert.equal(env.calls.dialogConfirm.length, 1);
  assert.equal(env.calls.fetch.length, 1);
});
