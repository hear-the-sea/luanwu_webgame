const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function createElement(id) {
  return {
    id,
    replacedBy: null,
    replaceWith(nextEl) {
      this.replacedBy = nextEl;
    },
  };
}

function createMetaTag(content) {
  return {
    _content: content,
    getAttribute(name) {
      return name === "content" ? this._content : null;
    },
    setAttribute(name, value) {
      if (name === "content") {
        this._content = value;
      }
    },
  };
}

function createDocument(options) {
  const {
    title = "春秋乱世庄园主",
    navLinks = [],
    csrfContent = "",
    titleText = title,
    extraScripts = null,
  } = options || {};
  const elements = new Map([
    ["main-nav", createElement("main-nav")],
    ["info-bar", createElement("info-bar")],
    ["page-shell", createElement("page-shell")],
  ]);
  const csrfMeta = createMetaTag(csrfContent);

  if (extraScripts) {
    elements.set("page-extra-scripts", extraScripts);
  }

  return {
    title,
    head: {
      childNodes: [],
      insertBefore() {},
    },
    body: {
      appendChild(node) {
        return node;
      },
    },
    createElement() {
      return {
        textContent: "",
        remove() {},
      };
    },
    querySelectorAll(selector) {
      if (selector === '.game-nav a.nav-tab[data-partial-nav="1"]') {
        return navLinks;
      }
      if (selector === "script[src]") {
        return [];
      }
      return [];
    },
    querySelector(selector) {
      if (selector === "title") {
        return { textContent: titleText };
      }
      if (selector === 'meta[name="csrf-token"]') {
        return csrfMeta;
      }
      return null;
    },
    getElementById(id) {
      return elements.get(id) || null;
    },
    addEventListener(type, listener) {
      this._listeners.set(type, listener);
    },
    dispatchEvent(event) {
      this._events.push(event);
      return true;
    },
    _listeners: new Map(),
    _events: [],
    _csrfMeta: csrfMeta,
  };
}

function createNavLink(href) {
  return {
    href,
    target: "",
    getAttribute(name) {
      if (name === "href") {
        return href;
      }
      return null;
    },
    hasAttribute() {
      return false;
    },
  };
}

function createClickEvent(link) {
  return {
    button: 0,
    metaKey: false,
    ctrlKey: false,
    shiftKey: false,
    altKey: false,
    defaultPrevented: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
    target: {
      closest(selector) {
        if (selector === '.game-nav a.nav-tab[data-partial-nav="1"]') {
          return link;
        }
        return null;
      },
    },
  };
}

async function flushAsyncWork() {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

test("partial navigation keeps the browser title fixed to the game name", async () => {
  const scriptPath = path.resolve(__dirname, "..", "nav_partial.js");
  const scriptSource = fs.readFileSync(scriptPath, "utf8");
  const currentDocument = createDocument({
    title: "春秋乱世庄园主",
    navLinks: [createNavLink("https://example.com/manor/guests/")],
    csrfContent: "old-token",
  });
  const nextDocument = createDocument({
    titleText: "门客列表",
    csrfContent: "new-token",
  });
  const fetchCalls = [];
  const historyCalls = [];
  const windowListeners = new Map();

  const context = {
    console,
    URL,
    Node: {
      COMMENT_NODE: 8,
      ELEMENT_NODE: 1,
    },
    Event: class Event {
      constructor(type) {
        this.type = type;
      }
    },
    CustomEvent: class CustomEvent {
      constructor(type, init) {
        this.type = type;
        this.detail = init && init.detail;
      }
    },
    DOMParser: class DOMParser {
      parseFromString() {
        return nextDocument;
      }
    },
    fetch: async (url) => {
      fetchCalls.push(url);
      return {
        ok: true,
        headers: {
          get(name) {
            return name === "content-type" ? "text/html; charset=utf-8" : null;
          },
        },
        async text() {
          return "<html></html>";
        },
      };
    },
    document: currentDocument,
    window: {
      fetch: true,
      DOMParser: true,
      location: {
        href: "https://example.com/manor/dashboard/",
        origin: "https://example.com",
      },
      history: {
        pushState(_state, _unused, url) {
          historyCalls.push(url);
        },
      },
      addEventListener(type, listener) {
        windowListeners.set(type, listener);
      },
      scrollTo() {},
      PartialNavCore: {
        async runPageScripts() {},
      },
    },
  };
  context.window.window = context.window;
  context.window.document = currentDocument;

  vm.createContext(context);
  vm.runInContext(scriptSource, context, { filename: scriptPath });

  const clickListener = currentDocument._listeners.get("click");
  assert.ok(clickListener, "expected nav_partial.js to register a click listener");

  const link = createNavLink("https://example.com/manor/guests/");
  clickListener(createClickEvent(link));
  await flushAsyncWork();

  assert.deepEqual(fetchCalls, ["https://example.com/manor/guests/"]);
  assert.deepEqual(historyCalls, ["https://example.com/manor/guests/"]);
  assert.equal(currentDocument.title, "春秋乱世庄园主");
  assert.equal(currentDocument._csrfMeta.getAttribute("content"), "new-token");
});
