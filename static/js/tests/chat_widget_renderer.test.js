const test = require("node:test");
const assert = require("node:assert/strict");

global.WorldChatWidgetCore = require("../chat_widget_core.js");
const chatWidgetRenderer = require("../chat_widget_renderer.js");

class FakeClassList {
  add() {}
}

class FakeElement {
  constructor() {
    this.children = [];
    this.classList = new FakeClassList();
    this.dataset = {};
    this.scrollHeight = 0;
    this.scrollTop = 0;
    this.clientHeight = 0;
    this.textContent = "";
  }

  get childElementCount() {
    return this.children.length;
  }

  get firstElementChild() {
    return this.children[0] || null;
  }

  appendChild(child) {
    if (child && child.isFragment) {
      this.children.push(...child.children);
      this.scrollHeight = this.children.length;
      return child;
    }
    this.children.push(child);
    this.scrollHeight = this.children.length;
    return child;
  }

  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) this.children.splice(index, 1);
    return child;
  }
}

test("renderer deduplicates by operation id and retains legacy id fallback", () => {
  const originalDocument = global.document;
  global.document = { createElement: () => new FakeElement() };
  try {
    const messagesEl = new FakeElement();
    const renderer = chatWidgetRenderer.createRenderer({
      getIsOpen: () => true,
      maxDomMessages: 20,
      messageTtlMs: 60_000,
      messagesEl,
      setUnreadDot() {},
      userId: 1,
    });

    renderer.handlePayload({
      type: "message",
      id: "redis-1",
      operation_id: "operation-1",
      sender: { id: 2, name: "A" },
      text: "first",
      ts: Date.now(),
    });
    renderer.handlePayload({
      type: "message",
      id: "redis-2",
      operation_id: "operation-1",
      sender: { id: 2, name: "A" },
      text: "duplicate",
      ts: Date.now(),
    });
    renderer.handlePayload({
      type: "message",
      id: "redis-3",
      operation_id: "operation-1",
      sender: { id: 3, name: "B" },
      text: "different sender",
      ts: Date.now(),
    });
    renderer.handlePayload({
      type: "message",
      id: "legacy-1",
      sender: { id: 3, name: "B" },
      text: "legacy",
      ts: Date.now(),
    });
    renderer.handlePayload({
      type: "message",
      id: "legacy-1",
      sender: { id: 3, name: "B" },
      text: "legacy duplicate",
      ts: Date.now(),
    });

    assert.equal(messagesEl.children.length, 3);
  } finally {
    global.document = originalDocument;
  }
});

test("history rendering batches DOM maintenance before applying the message cap", () => {
  const originalDocument = global.document;
  class FakeFragment extends FakeElement {
    constructor() {
      super();
      this.isFragment = true;
    }
  }

  global.document = {
    createElement: () => new FakeElement(),
    createDocumentFragment: () => new FakeFragment(),
  };
  try {
    const messagesEl = new FakeElement();
    const renderer = chatWidgetRenderer.createRenderer({
      getIsOpen: () => true,
      maxDomMessages: 2,
      messageTtlMs: 60_000,
      messagesEl,
      setUnreadDot() {},
      userId: 1,
    });

    renderer.handlePayload({
      type: "history",
      messages: [
        { type: "message", id: "history-1", sender: { id: 2, name: "A" }, text: "one", ts: Date.now() },
        { type: "message", id: "history-2", sender: { id: 2, name: "A" }, text: "two", ts: Date.now() },
        { type: "message", id: "history-3", sender: { id: 2, name: "A" }, text: "three", ts: Date.now() },
      ],
    });

    assert.equal(messagesEl.children.length, 2);
  } finally {
    global.document = originalDocument;
  }
});
