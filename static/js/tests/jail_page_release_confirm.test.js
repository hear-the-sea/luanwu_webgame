const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "jail-page.js"), "utf8");

const flushPromises = () => new Promise((resolve) => setImmediate(resolve));

function createHarness({
  confirmed = false,
  deferred = false,
  sharedDialog = true,
  fetchPayload = null,
  deferredFetch = false,
  fetchError = "",
  deferredErrorDialog = false,
  confirmError = "",
} = {}) {
  const listeners = {};
  const confirmCalls = [];
  const successCalls = [];
  const errorCalls = [];
  const alertCalls = [];
  const fetchCalls = [];
  let reloadCalls = 0;
  let resolveConfirmation;
  let resolveFetch;
  let resolveErrorDialog;
  const confirmationPromise = deferred
    ? new Promise((resolve) => {
        resolveConfirmation = resolve;
      })
    : null;
  const errorDialogPromise = deferredErrorDialog
    ? new Promise((resolve) => {
        resolveErrorDialog = resolve;
      })
    : null;
  const fetchResponse = () => ({
    ok: !fetchError,
    json: async () => (fetchError ? { success: false, error: fetchError } : fetchPayload),
  });
  const fetchPromise = deferredFetch
    ? new Promise((resolve) => {
        resolveFetch = () => resolve(fetchResponse());
      })
    : null;
  const root = {
    dataset: {},
    addEventListener(type, handler) {
      listeners[type] = handler;
    },
  };
  const windowObject = {
    JailPageCore: {
      buildInteractionPayload(method) {
        return { method };
      },
      formatDeltaSummary() {
        return "";
      },
      formatRecruitmentSummary(payload) {
        return payload?.recruited === true && payload.initial_loyalty != null
          ? `已成为 1 级门客｜初始忠诚 ${payload.initial_loyalty}`
          : "";
      },
      formatHistoryEntries() {
        return "尚无招降记录。";
      },
    },
    alert() {},
    confirm(message) {
      confirmCalls.push({ message, options: null, source: "native" });
      return confirmed;
    },
    location: {
      reload() {
        reloadCalls += 1;
      },
    },
  };
  if (sharedDialog) {
    windowObject.gameConfirm = (message, options) => {
      confirmCalls.push({ message, options, source: "shared" });
      if (confirmError) {
        return Promise.reject(new Error(confirmError));
      }
      return confirmationPromise || Promise.resolve(confirmed);
    };
    windowObject.gameDialog = {
      success(message, options) {
        successCalls.push({ message, options });
        return Promise.resolve();
      },
      error(message, options) {
        errorCalls.push({ message, options });
        return errorDialogPromise || Promise.resolve();
      },
      alert(message, options) {
        alertCalls.push({ message, options });
        return Promise.resolve();
      },
    };
  }
  const documentObject = {
    querySelector(selector) {
      return selector === '[data-jail-root="1"]' ? root : null;
    },
  };

  vm.runInNewContext(source, {
    window: windowObject,
    document: documentObject,
    Promise,
    Array,
    Error,
    Number,
    String,
    JSON,
    fetch: async (url, options) => {
      fetchCalls.push({ url, options });
      return fetchPromise || fetchResponse();
    },
  }, { filename: "jail-page.js" });

  return {
    listeners,
    confirmCalls,
    successCalls,
    errorCalls,
    alertCalls,
    fetchCalls,
    reloadCalls: () => reloadCalls,
    resolveConfirmation,
    resolveFetch,
    resolveErrorDialog,
  };
}

function createReleaseSubmission(prisonerName = "成昆", dossierState = createDossier(), initiallyDisabled = false) {
  let submitted = 0;
  let focusCalls = 0;
  const releaseButton = {
    dataset: {},
    disabled: initiallyDisabled,
    focus() {
      focusCalls += 1;
    },
  };
  const form = {
    dataset: { releasePrisonerName: prisonerName },
    closest(selector) {
      if (selector === "form[data-jail-release-form]") {
        return form;
      }
      if (selector === "[data-prisoner-id]") {
        return dossierState.dossier;
      }
      return null;
    },
    querySelector(selector) {
      return selector === 'button[type="submit"]' ? releaseButton : null;
    },
    submit() {
      submitted += 1;
    },
  };
  dossierState.buttons.push(releaseButton);
  const event = {
    target: form,
    defaultPrevented: false,
    preventDefault() {
      event.defaultPrevented = true;
    },
  };

  return {
    dossier: dossierState.dossier,
    event,
    focusCalls: () => focusCalls,
    form,
    releaseButton,
    submitted: () => submitted,
  };
}

function createDossier() {
  const buttons = [];
  const classNames = new Set();
  const historyEntries = [];
  const dossier = {
    dataset: {},
    classList: {
      add(name) {
        classNames.add(name);
      },
      remove(name) {
        classNames.delete(name);
      },
      contains(name) {
        return classNames.has(name);
      },
    },
    querySelectorAll(selector) {
      if (selector === "button") {
        return buttons;
      }
      if (selector === "button[data-jail-action]") {
        return buttons.filter((button) => button.dataset.jailAction);
      }
      return selector === "[data-jail-history-item]" ? historyEntries : [];
    },
  };
  return { buttons, dossier, historyEntries };
}

function createActionClick({
  action = "recruit",
  dossierState = createDossier(),
  initiallyDisabled = false,
  mode = "standard",
} = {}) {
  let focusCalls = 0;
  const button = {
    dataset: {
      jailAction: action,
      mode,
      actionUrl: "/jail/1/recruit/api/",
    },
    disabled: initiallyDisabled,
    closest(selector) {
      if (selector === "button[data-jail-action]") {
        return button;
      }
      if (selector === "[data-prisoner-id]") {
        return dossierState.dossier;
      }
      return null;
    },
    focus() {
      focusCalls += 1;
    },
  };
  dossierState.buttons.push(button);
  const event = {
    target: button,
    defaultPrevented: false,
    preventDefault() {
      event.defaultPrevented = true;
    },
  };
  return {
    button,
    dossier: dossierState.dossier,
    dossierState,
    event,
    focusCalls: () => focusCalls,
  };
}

function createRecruitClick(dossierState = createDossier()) {
  return createActionClick({ dossierState });
}

function createHistoryClick(dossierState = createDossier()) {
  const button = {
    dataset: { prisonerName: "成昆" },
    disabled: false,
    closest(selector) {
      if (selector === "button[data-jail-history-open]") {
        return button;
      }
      if (selector === "[data-prisoner-id]") {
        return dossierState.dossier;
      }
      return null;
    },
  };
  dossierState.buttons.push(button);
  const event = {
    target: button,
    defaultPrevented: false,
    preventDefault() {
      event.defaultPrevented = true;
    },
  };
  return { button, dossier: dossierState.dossier, event };
}

test("release cancellation keeps the prisoner and names the irreversible action", async () => {
  const harness = createHarness({ confirmed: false });
  const submission = createReleaseSubmission();

  assert.equal(typeof harness.listeners.submit, "function");
  harness.listeners.submit(submission.event);
  await flushPromises();

  assert.equal(submission.event.defaultPrevented, true);
  assert.equal(submission.submitted(), 0);
  assert.equal(harness.confirmCalls.length, 1);
  assert.match(harness.confirmCalls[0].message, /成昆/);
  assert.match(harness.confirmCalls[0].message, /无法撤回/);
  assert.equal(harness.confirmCalls[0].options.title, "确认释放");
  assert.equal(harness.confirmCalls[0].options.okText, "确认释放");
  assert.equal(submission.focusCalls(), 1);
  assert.equal(submission.dossier.dataset.jailActionPending, undefined);
});

test("release confirmation ignores duplicate submits and submits the form once", async () => {
  const harness = createHarness({ deferred: true });
  const submission = createReleaseSubmission("西门吹雪");

  assert.equal(typeof harness.listeners.submit, "function");
  harness.listeners.submit(submission.event);
  harness.listeners.submit(submission.event);
  assert.equal(harness.confirmCalls.length, 1);
  assert.equal(submission.dossier.dataset.jailActionPending, "1");

  harness.resolveConfirmation(true);
  await flushPromises();

  assert.equal(submission.submitted(), 1);
  assert.equal(submission.dossier.dataset.jailActionPending, "1");
});

test("release confirmation falls back to the native confirm dialog", async () => {
  const harness = createHarness({ confirmed: true, sharedDialog: false });
  const submission = createReleaseSubmission("花满楼");

  assert.equal(typeof harness.listeners.submit, "function");
  harness.listeners.submit(submission.event);
  await flushPromises();

  assert.equal(harness.confirmCalls[0].source, "native");
  assert.equal(submission.submitted(), 1);
});

test("recruit failure shows only its story and reloads after acknowledgement", async () => {
  const story = "他沉默许久，最终仍未应允。";
  const harness = createHarness({
    confirmed: true,
    fetchPayload: {
      success: true,
      recruited: false,
      guest_id: null,
      mode: "standard",
      initial_loyalty: null,
      gold_cost: 1,
      copy_key: "recruitment.failure.standard.1",
      copy_params: { prisoner_name: "成昆", new_loyalty: 0 },
      text: story,
    },
  });
  const click = createRecruitClick();

  harness.listeners.click(click.event);
  await flushPromises();
  await flushPromises();

  assert.equal(click.event.defaultPrevented, true);
  assert.equal(harness.fetchCalls.length, 1);
  assert.equal(harness.successCalls.length, 1);
  assert.equal(harness.successCalls[0].options.title, "归附未成");
  assert.equal(harness.successCalls[0].message, story);
  assert.equal(harness.reloadCalls(), 1);
});

test("recruit confirmation explains both costs without exposing odds", async () => {
  const harness = createHarness({ confirmed: false });
  const click = createRecruitClick();

  harness.listeners.click(click.event);
  await flushPromises();

  assert.equal(harness.confirmCalls.length, 1);
  assert.match(harness.confirmCalls[0].message, /消耗所示金条/);
  assert.match(harness.confirmCalls[0].message, /今日的归附尝试/);
  assert.doesNotMatch(harness.confirmCalls[0].message, /概率|胜算|[%％]/);
  assert.equal(harness.fetchCalls.length, 0);
});

test("delayed recruit confirmation ignores repeated clicks", async () => {
  const harness = createHarness({ deferred: true });
  const click = createRecruitClick();

  harness.listeners.click(click.event);
  harness.listeners.click(click.event);

  assert.equal(harness.confirmCalls.length, 1);
  harness.resolveConfirmation(false);
  await flushPromises();
});

test("cancelled recruit confirmation restores focus to its trigger", async () => {
  const harness = createHarness({ confirmed: false });
  const click = createRecruitClick();

  harness.listeners.click(click.event);
  await flushPromises();

  assert.equal(click.focusCalls(), 1);
  assert.equal(click.dossier.dataset.jailActionPending, undefined);
});

test("pending request blocks every action for the same prisoner", async () => {
  const harness = createHarness({ confirmed: true, deferredFetch: true, fetchPayload: { success: true } });
  const dossierState = createDossier();
  const recruit = createRecruitClick(dossierState);
  const observe = createActionClick({ action: "observe", dossierState });

  harness.listeners.click(recruit.event);
  await flushPromises();

  assert.equal(harness.fetchCalls.length, 1);
  assert.equal(recruit.dossier.classList.contains("jail-action-pending"), true);
  assert.equal(recruit.button.disabled, true);
  assert.equal(observe.button.disabled, true);

  harness.listeners.click(observe.event);
  await flushPromises();
  assert.equal(harness.fetchCalls.length, 1);

  harness.resolveFetch();
  await flushPromises();
  await flushPromises();
});

test("request error restores prior controls after the dialog and focuses its trigger", async () => {
  const harness = createHarness({
    confirmed: true,
    fetchError: "请求失败",
    deferredErrorDialog: true,
  });
  const dossierState = createDossier();
  const recruit = createRecruitClick(dossierState);
  const availableAction = createActionClick({ action: "observe", dossierState });
  const serverDisabledAction = createActionClick({
    action: "observe",
    dossierState,
    initiallyDisabled: true,
  });

  harness.listeners.click(recruit.event);
  await flushPromises();
  await flushPromises();

  assert.equal(harness.errorCalls.length, 1);
  assert.equal(recruit.focusCalls(), 0);
  assert.equal(recruit.button.disabled, true);
  assert.equal(availableAction.button.disabled, true);
  assert.equal(serverDisabledAction.button.disabled, true);

  harness.resolveErrorDialog();
  await flushPromises();

  assert.equal(recruit.button.disabled, false);
  assert.equal(availableAction.button.disabled, false);
  assert.equal(serverDisabledAction.button.disabled, true);
  assert.equal(recruit.focusCalls(), 1);
  assert.equal(recruit.dossier.dataset.jailActionPending, undefined);
});

test("pending JSON request blocks release submit and history dialog for its prisoner", async () => {
  const harness = createHarness({ confirmed: true, deferredFetch: true, fetchPayload: { success: true } });
  const dossierState = createDossier();
  const recruit = createRecruitClick(dossierState);
  const release = createReleaseSubmission("成昆", dossierState);
  const history = createHistoryClick(dossierState);

  harness.listeners.click(recruit.event);
  await flushPromises();

  assert.equal(release.releaseButton.disabled, true);
  assert.equal(history.button.disabled, true);
  harness.listeners.submit(release.event);
  harness.listeners.click(history.event);
  await flushPromises();

  assert.equal(release.event.defaultPrevented, true);
  assert.equal(history.event.defaultPrevented, true);
  assert.equal(harness.confirmCalls.length, 1);
  assert.equal(release.submitted(), 0);
  assert.equal(harness.alertCalls.length, 0);

  harness.resolveFetch();
  await flushPromises();
  await flushPromises();
});

test("pending release confirmation blocks JSON actions for its prisoner", async () => {
  const harness = createHarness({ deferred: true });
  const dossierState = createDossier();
  const release = createReleaseSubmission("成昆", dossierState);
  const recruit = createRecruitClick(dossierState);

  harness.listeners.submit(release.event);
  harness.listeners.click(recruit.event);
  await flushPromises();

  assert.equal(harness.confirmCalls.length, 1);
  assert.equal(harness.fetchCalls.length, 0);
  assert.equal(release.dossier.dataset.jailActionPending, "1");

  harness.resolveConfirmation(false);
  await flushPromises();
});

test("release confirmation error clears its locks and restores submit focus", async () => {
  const harness = createHarness({ confirmError: "确认弹窗失败" });
  const submission = createReleaseSubmission();

  harness.listeners.submit(submission.event);
  await flushPromises();
  await flushPromises();

  assert.equal(harness.errorCalls.length, 1);
  assert.equal(submission.submitted(), 0);
  assert.equal(submission.form.dataset.releaseConfirmPending, undefined);
  assert.equal(submission.dossier.dataset.jailActionPending, undefined);
  assert.equal(submission.focusCalls(), 1);
});
