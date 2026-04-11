const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function createElement({ id = "", dataset = {}, style = {}, action = "", textContent = "" } = {}) {
  const listeners = new Map();
  return {
    id,
    dataset,
    style,
    action,
    textContent,
    disabled: false,
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
    dispatchEvent(event) {
      const listener = listeners.get(event.type);
      if (listener) {
        listener.call(this, event);
      }
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
  };
}

test("guest roster salary button still works after partial navigation load", () => {
  const scriptPath = path.resolve(__dirname, "..", "guest-roster.js");
  const scriptSource = fs.readFileSync(scriptPath, "utf8");

  const salaryModal = createElement({ id: "salary-modal", style: { display: "none" } });
  const salaryForm = createElement({ id: "salary-form" });
  const salaryGuestName = createElement({ id: "salary-guest-name" });
  const salaryAmount = createElement({ id: "salary-amount" });
  const salaryConfirmBtn = createElement({ id: "salary-confirm-btn" });
  const dashboardRoot = createElement({ id: "dashboard-root", dataset: {} });
  const salaryButton = createElement({
    dataset: {
      guestId: "42",
      guestName: "测试门客",
      salary: "88",
      canPay: "true",
    },
  });

  const documentListeners = new Map();
  const elementById = new Map([
    ["salary-modal", salaryModal],
    ["salary-form", salaryForm],
    ["salary-guest-name", salaryGuestName],
    ["salary-amount", salaryAmount],
    ["salary-confirm-btn", salaryConfirmBtn],
  ]);

  const documentObj = {
    getElementById(id) {
      return elementById.get(id) || null;
    },
    querySelectorAll(selector) {
      if (selector === ".open-salary-modal") {
        return [salaryButton];
      }
      return [];
    },
    querySelector(selector) {
      if (selector === ".dashboard") {
        return dashboardRoot;
      }
      return null;
    },
    addEventListener(type, listener) {
      documentListeners.set(type, listener);
    },
    dispatchEvent(event) {
      const listener = documentListeners.get(event.type);
      if (listener) {
        listener.call(this, event);
      }
      return true;
    },
    createElement() {
      return createElement();
    },
    body: {
      appendChild(node) {
        return node;
      },
      insertBefore(node) {
        return node;
      },
    },
  };

  const context = {
    console,
    document: documentObj,
    window: {
      setTimeout,
      confirm() {
        return true;
      },
    },
    FormData,
    fetch: async () => ({ json: async () => ({ success: true }) }),
  };
  context.window.window = context.window;
  context.window.document = documentObj;
  vm.createContext(context);

  vm.runInContext(scriptSource, context, { filename: "guest-roster.js" });

  documentObj.dispatchEvent({ type: "partial-nav:loaded" });
  salaryButton.dispatchEvent({ type: "click" });

  assert.equal(salaryModal.style.display, "flex");
  assert.equal(salaryForm.action, "/guests/42/pay-salary/");
  assert.equal(salaryGuestName.textContent, "测试门客");
  assert.equal(salaryAmount.textContent, "88 银两");
  assert.equal(salaryConfirmBtn.disabled, false);
});
