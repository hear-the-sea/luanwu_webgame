const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function createElement(tagName) {
  const listeners = new Map();
  const element = {
    tagName,
    className: "",
    textContent: "",
    attributes: new Map(),
    children: [],
    style: {},
    dataset: {},
    classList: {
      values: new Set(),
      add(value) {
        this.values.add(value);
        element.className = Array.from(this.values).join(" ");
      },
      contains(value) {
        return this.values.has(value);
      },
    },
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    remove() {
      this.removed = true;
    },
    setAttribute(name, value) {
      this.attributes.set(name, value);
    },
    getAttribute(name) {
      return this.attributes.get(name);
    },
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
    dispatchEvent(event) {
      const listener = listeners.get(event.type);
      if (listener) {
        listener.call(this, event);
      }
    },
    querySelector(selector) {
      const className = selector.startsWith(".") ? selector.slice(1) : selector;
      const directMatch = this.children.find((child) => child.className.split(" ").includes(className));
      if (directMatch) {
        return directMatch;
      }
      for (const child of this.children) {
        const nestedMatch = child.querySelector(selector);
        if (nestedMatch) {
          return nestedMatch;
        }
      }
      return null;
    },
  };
  return element;
}

test("notification toast uses unified semantic structure", () => {
  const scriptPath = path.resolve(__dirname, "..", "notifications.js");
  const scriptSource = fs.readFileSync(scriptPath, "utf8");
  const timers = [];
  const messagesLink = createElement("a");
  messagesLink.dataset.unread = "0";
  const toastContainer = createElement("div");

  const documentObj = {
    getElementById(id) {
      if (id === "nav-messages-link") return messagesLink;
      if (id === "toast-container") return toastContainer;
      return null;
    },
    createElement,
    addEventListener(type, listener) {
      if (type === "DOMContentLoaded") {
        listener();
      }
    },
  };

  class FakeWebSocket {
    constructor() {
      FakeWebSocket.instances.push(this);
    }

    close() {}
  }
  FakeWebSocket.instances = [];

  const context = {
    document: documentObj,
    window: {
      location: {
        protocol: "http:",
        host: "example.test",
        pathname: "/gameplay/recruitment/",
      },
      setTimeout(fn) {
        timers.push(fn);
        return timers.length;
      },
      WebSocket: FakeWebSocket,
    },
    WebSocket: FakeWebSocket,
    setTimeout(fn) {
      timers.push(fn);
      return timers.length;
    },
  };
  context.window.window = context.window;
  context.window.document = documentObj;
  vm.createContext(context);

  vm.runInContext(scriptSource, context, { filename: "notifications.js" });

  FakeWebSocket.instances[0].onmessage({
    data: JSON.stringify({
      kind: "system",
      title: "招募完成",
      body: "门客候选已更新",
    }),
  });

  const toast = toastContainer.children[0];
  assert.ok(toast.className.includes("toast toast-system"));
  assert.equal(toast.getAttribute("role"), "status");
  assert.equal(toast.getAttribute("aria-live"), "polite");
  assert.equal(toast.querySelector(".toast-icon"), null);
  assert.equal(toast.querySelector(".toast-title").textContent, "招募完成");
  assert.equal(toast.querySelector(".toast-body").textContent, "门客候选已更新");
  assert.equal(toast.querySelector(".toast-close").getAttribute("aria-label"), "关闭通知");

  toast.querySelector(".toast-close").dispatchEvent({ type: "click" });

  assert.ok(toast.classList.contains("toast-leaving"));
});
