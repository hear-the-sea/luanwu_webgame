const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function createTasksPageHarness(confirmResult) {
  const source = fs.readFileSync(path.resolve(__dirname, "..", "tasks-page.js"), "utf8");
  const listeners = new Map();
  const calls = { confirm: [], submit: 0, preventDefault: 0 };
  const submitButton = { disabled: false };
  const form = {
    dataset: {
      missionName: "确认测试任务",
      cardCount: "4",
      usedCount: "1",
      dailyLimit: "5",
    },
    addEventListener(type, handler) {
      listeners.set(`form:${type}`, handler);
    },
    querySelector(selector) {
      return selector === 'button[type="submit"]' ? submitButton : null;
    },
    submit() {
      calls.submit += 1;
    },
  };
  const document = {
    addEventListener(type, handler) {
      listeners.set(type, handler);
    },
    querySelector(selector) {
      return selector === ".tw-mission-tabs" ? {} : null;
    },
    querySelectorAll(selector) {
      if (selector === ".js-mission-card-form") {
        return [form];
      }
      return [];
    },
  };
  const windowObject = {
    TasksPageCore: {
      buildMissionCardConfirmation(data) {
        return `确认 ${data.missionName} ${data.usedCount}/${data.dailyLimit}`;
      },
    },
    gameConfirm(message, options) {
      calls.confirm.push({ message, options });
      return Promise.resolve(confirmResult);
    },
    setTimeout,
  };

  vm.runInNewContext(
    source,
    { window: windowObject, document, console, setTimeout, clearTimeout },
    { filename: "tasks-page.js" }
  );
  listeners.get("DOMContentLoaded")();

  return {
    calls,
    form,
    submitButton,
    async submit() {
      await listeners.get("form:submit")({
        preventDefault() {
          calls.preventDefault += 1;
        },
      });
    },
  };
}

test("mission card form asks for confirmation before submitting", async () => {
  const harness = createTasksPageHarness(true);

  await harness.submit();

  assert.equal(harness.calls.confirm.length, 1);
  assert.equal(harness.calls.confirm[0].message, "确认 确认测试任务 1/5");
  assert.equal(harness.calls.confirm[0].options.title, "使用任务卡");
  assert.equal(harness.calls.confirm[0].options.okText, "确认使用");
  assert.equal(harness.calls.preventDefault, 1);
  assert.equal(harness.calls.submit, 1);
  assert.equal(harness.submitButton.disabled, true);
});

test("mission card form remains idle when confirmation is cancelled", async () => {
  const harness = createTasksPageHarness(false);

  await harness.submit();

  assert.equal(harness.calls.confirm.length, 1);
  assert.equal(harness.calls.submit, 0);
  assert.equal(harness.submitButton.disabled, false);
  assert.equal(harness.form.dataset.confirmPending, undefined);
});
