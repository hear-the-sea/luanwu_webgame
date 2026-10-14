const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

test("chat widget page lifecycle suspends and restores its connection and pruning timer", () => {
  const listeners = new Map();
  const intervals = new Map();
  let nextInterval = 1;
  const calls = { connect: 0, suspend: 0, resume: 0 };
  const elements = new Map();
  for (const id of [
    "chat-widget", "chat-fab", "chat-fab-badge", "chat-panel", "chat-status",
    "chat-messages", "chat-input", "chat-send", "chat-close", "chat-clear", "chat-header",
  ]) {
    elements.set(id, {
      dataset: {},
      value: "",
      classList: { toggle() {}, remove() {}, add() {} },
      addEventListener() {},
      setAttribute() {},
      querySelector() { return elements.get("chat-header"); },
    });
  }
  const context = {
    document: {
      getElementById(id) { return elements.get(id); },
      addEventListener() {},
    },
    localStorage: { getItem() { return null; }, setItem() {} },
    setInterval(callback) {
      const id = nextInterval++;
      intervals.set(id, callback);
      return id;
    },
    clearInterval(id) { intervals.delete(id); },
    window: {
      WebSocket() {},
      location: { protocol: "https:", host: "example.test" },
      addEventListener(type, listener) { listeners.set(type, listener); },
      WorldChatWidgetCore: { buildWebSocketUrl() { return "wss://example.test/ws/chat/world/"; } },
      WorldChatWidgetRenderer: {
        createRenderer() { return { pruneOldMessages() {} }; },
      },
      WorldChatWidgetLayout: {
        createLayoutController() { return { loadWidgetPos() {} }; },
      },
      WorldChatWidgetConnection: {
        createConnectionController() {
          return {
            connect() { calls.connect++; },
            suspend() { calls.suspend++; },
            resume() { calls.resume++; },
          };
        },
      },
    },
  };
  vm.runInNewContext(
    fs.readFileSync(path.resolve(__dirname, "..", "chat_widget.js"), "utf8"),
    context,
  );
  assert.equal(calls.connect, 1);
  assert.equal(intervals.size, 1);
  assert.equal(listeners.has("visibilitychange"), false);
  listeners.get("pagehide")({ persisted: true });
  listeners.get("pagehide")({ persisted: true });
  assert.equal(calls.suspend, 2);
  assert.equal(intervals.size, 0);
  listeners.get("pageshow")({ persisted: true });
  listeners.get("pageshow")({ persisted: true });
  assert.equal(calls.resume, 2);
  assert.equal(intervals.size, 1);
  assert.equal(calls.connect, 1);
});
