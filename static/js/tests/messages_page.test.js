const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function loadMessagesPage() {
  return fs.readFileSync(path.join(__dirname, "../messages-page.js"), "utf8");
}

function createMessagesPageEnv() {
  const listeners = new Map();
  const calls = {
    fetch: [],
    location: [],
    preventDefault: 0,
  };

  const csrfInput = { value: "csrf-token" };
  const unreadCount = {
    dataset: { unread: "2" },
    textContent: "2",
  };
  const newBadge = {
    removed: false,
    remove() {
      this.removed = true;
    },
  };
  const messageRow = {
    classList: {
      removed: [],
      remove(className) {
        this.removed.push(className);
      },
    },
  };
  const messageForm = {
    dataset: { markReadUrl: "/messages/mark/" },
  };
  const link = {
    dataset: { isRead: "false", messageId: "42" },
    getAttribute(name) {
      return name === "href" ? "/messages/view/42/" : null;
    },
    addEventListener(type, handler) {
      listeners.set(`link:${type}`, handler);
    },
    closest(selector) {
      return selector === "tr" ? messageRow : null;
    },
    parentElement: {
      querySelector(selector) {
        return selector === ".msg-badge-new" ? newBadge : null;
      },
    },
  };

  const documentStub = {
    addEventListener(type, handler) {
      listeners.set(type, handler);
    },
    querySelector(selector) {
      if (selector === ".dashboard") {
        return {};
      }
      if (selector === "input[name='csrfmiddlewaretoken']") {
        return csrfInput;
      }
      return null;
    },
    querySelectorAll(selector) {
      if (selector === ".js-message-link") {
        return [link];
      }
      if (selector === ".js-claim-attachment") {
        return [];
      }
      return [];
    },
    getElementById(id) {
      if (id === "message-form") {
        return messageForm;
      }
      if (id === "unread-count") {
        return unreadCount;
      }
      return null;
    },
  };

  const windowStub = {
    setTimeout(callback) {
      return callback;
    },
    clearTimeout() {},
    location: {
      set href(value) {
        calls.location.push(value);
      },
    },
  };

  const context = {
    window: windowStub,
    document: documentStub,
    FormData: class FakeFormData {
      constructor() {
        this.entries = [];
      }

      append(key, value) {
        this.entries.push([key, value]);
      }
    },
    AbortController: class FakeAbortController {
      constructor() {
        this.signal = {};
      }
    },
    fetch(url, options) {
      calls.fetch.push({ url, options });
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ success: true, unread_count: 1 }),
      });
    },
    console,
  };

  function runScript() {
    vm.runInNewContext(loadMessagesPage(), context, { filename: "messages-page.js" });
    listeners.get("DOMContentLoaded")();
  }

  async function clickMessageLink() {
    const handler = listeners.get("link:click");
    assert.equal(typeof handler, "function");
    handler({
      button: 0,
      defaultPrevented: false,
      preventDefault() {
        calls.preventDefault += 1;
      },
    });
    await new Promise((resolve) => setImmediate(resolve));
  }

  return {
    calls,
    clickMessageLink,
    link,
    messageRow,
    newBadge,
    runScript,
    unreadCount,
  };
}

test("messages page marks unread message through explicit POST action before navigation", async () => {
  const env = createMessagesPageEnv();

  env.runScript();
  await env.clickMessageLink();

  assert.equal(env.calls.preventDefault, 1);
  assert.equal(env.calls.fetch.length, 1);
  assert.equal(env.calls.fetch[0].url, "/messages/mark/");
  assert.equal(env.calls.fetch[0].options.method, "POST");
  assert.equal(env.calls.fetch[0].options.headers.Accept, "application/json");
  assert.equal(env.calls.fetch[0].options.headers["X-CSRFToken"], "csrf-token");
  assert.deepEqual(env.calls.fetch[0].options.body.entries, [["message_ids", "42"]]);
  assert.equal(env.unreadCount.textContent, "1");
  assert.equal(env.link.dataset.isRead, "true");
  assert.deepEqual(env.messageRow.classList.removed, ["unread"]);
  assert.equal(env.newBadge.removed, true);
  assert.deepEqual(env.calls.location, ["/messages/view/42/"]);
});
