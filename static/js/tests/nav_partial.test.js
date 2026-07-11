const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function createElement(id, attributes = {}) {
  return {
    id,
    replacedBy: null,
    _attributes: new Map(Object.entries(attributes)),
    getAttribute(name) {
      return this._attributes.get(name) ?? null;
    },
    setAttribute(name, value) {
      this._attributes.set(name, String(value));
    },
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
    authenticated = true,
    includeAuthenticatedMarker = true,
    missingSectionIds = [],
  } = options || {};
  const elements = new Map();
  for (const id of ["main-nav", "info-bar", "page-shell"]) {
    if (missingSectionIds.includes(id)) continue;
    const attributes = id === "page-shell" && includeAuthenticatedMarker
      ? { "data-authenticated": authenticated ? "1" : "0" }
      : {};
    elements.set(id, createElement(id, attributes));
  }
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
    _elements: elements,
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

function createPartialNavHarness({
  initialHref = "https://example.com/manor/dashboard/",
  allowedHrefs = [
    "https://example.com/manor/guests/",
    "https://example.com/manor/warehouse/",
  ],
  runPageScripts = null,
} = {}) {
  const scriptPath = path.resolve(__dirname, "..", "nav_partial.js");
  const scriptSource = fs.readFileSync(scriptPath, "utf8");
  const currentDocument = createDocument({
    title: "春秋乱世庄园主",
    navLinks: allowedHrefs.map(createNavLink),
    csrfContent: "old-token",
  });
  const fetchCalls = [];
  const historyCalls = [];
  const scriptCalls = [];
  const consoleErrors = [];
  const windowListeners = new Map();
  const responseQueue = [];
  const parsedDocuments = new Map();
  let fetchImplementation = null;
  let responseSequence = 0;

  const location = {
    href: initialHref,
    origin: new URL(initialHref).origin,
  };

  const context = {
    AbortController,
    console: {
      error(...args) {
        consoleErrors.push(args);
      },
    },
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
      parseFromString(html) {
        return parsedDocuments.get(html);
      }
    },
    fetch: async (url) => {
      fetchCalls.push(url);
      if (fetchImplementation) return fetchImplementation(url);
      assert.ok(responseQueue.length > 0, `missing response for ${url}`);
      return responseQueue.shift();
    },
    document: currentDocument,
    window: {
      AbortController,
      fetch: true,
      DOMParser: true,
      location,
      history: {
        pushState(_state, _unused, url) {
          historyCalls.push(url);
          location.href = new URL(url, location.href).href;
        },
      },
      addEventListener(type, listener) {
        windowListeners.set(type, listener);
      },
      scrollTo() {},
      PartialNavCore: {
        async runPageScripts(options) {
          scriptCalls.push(options);
          if (runPageScripts) {
            await runPageScripts(options);
          }
        },
      },
    },
  };
  context.window.window = context.window;
  context.window.document = currentDocument;

  vm.createContext(context);
  vm.runInContext(scriptSource, context, { filename: scriptPath });

  const clickListener = currentDocument._listeners.get("click");
  assert.ok(clickListener, "expected nav_partial.js to register a click listener");

  return {
    consoleErrors,
    currentDocument,
    fetchCalls,
    historyCalls,
    location,
    makeResponse(nextDocument, {
      redirected = false,
      responseUrl = "",
      ok = true,
      contentType = "text/html; charset=utf-8",
    } = {}) {
      responseSequence += 1;
      const htmlKey = `response-${responseSequence}`;
      parsedDocuments.set(htmlKey, nextDocument);
      return {
        ok,
        redirected,
        url: responseUrl,
        headers: {
          get(name) {
            return name === "content-type" ? contentType : null;
          },
        },
        async text() {
          return htmlKey;
        },
      };
    },
    navigate(href) {
      clickListener(createClickEvent(createNavLink(href)));
    },
    queueResponse(response) {
      responseQueue.push(response);
    },
    scriptCalls,
    setFetchImplementation(implementation) {
      fetchImplementation = implementation;
    },
  };
}

function assertNoPartialMutation(harness) {
  for (const id of ["main-nav", "info-bar", "page-shell"]) {
    assert.equal(harness.currentDocument._elements.get(id).replacedBy, null);
  }
  assert.deepEqual(harness.historyCalls, []);
  assert.equal(harness.scriptCalls.length, 0);
  assert.equal(
    harness.currentDocument._events.filter((event) => event.type === "partial-nav:loaded").length,
    0,
  );
}

test("partial navigation keeps the browser title fixed to the game name", async () => {
  const harness = createPartialNavHarness();
  const nextDocument = createDocument({ titleText: "门客列表", csrfContent: "new-token" });
  harness.queueResponse(harness.makeResponse(nextDocument));

  harness.navigate("https://example.com/manor/guests/");
  await flushAsyncWork();

  assert.deepEqual(harness.fetchCalls, ["https://example.com/manor/guests/"]);
  assert.deepEqual(harness.historyCalls, ["https://example.com/manor/guests/"]);
  assert.equal(harness.currentDocument.title, "春秋乱世庄园主");
  assert.equal(harness.currentDocument._csrfMeta.getAttribute("content"), "new-token");
});

test("redirected login response hard navigates before partial mutation", async () => {
  const harness = createPartialNavHarness();
  const loginUrl = "https://example.com/accounts/login/?next=/manor/guests/";
  const loginDocument = createDocument({ authenticated: false, extraScripts: {} });
  harness.queueResponse(harness.makeResponse(loginDocument, { redirected: true, responseUrl: loginUrl }));

  harness.navigate("https://example.com/manor/guests/");
  await flushAsyncWork();

  assert.equal(harness.location.href, loginUrl);
  assertNoPartialMutation(harness);
});

test("mismatched final response URL hard navigates even without redirected flag", async () => {
  const harness = createPartialNavHarness();
  const finalUrl = "https://example.com/accounts/login/?next=/manor/guests/#expired";
  const nextDocument = createDocument({ extraScripts: {} });
  harness.queueResponse(harness.makeResponse(nextDocument, { redirected: false, responseUrl: finalUrl }));

  harness.navigate("https://example.com/manor/guests/");
  await flushAsyncWork();

  assert.equal(harness.location.href, finalUrl);
  assertNoPartialMutation(harness);
});

test("final response URL comparison ignores hash-only differences", async () => {
  const harness = createPartialNavHarness();
  const targetUrl = "https://example.com/manor/guests/";
  const nextDocument = createDocument();
  harness.queueResponse(harness.makeResponse(nextDocument, { responseUrl: `${targetUrl}#details` }));

  harness.navigate(targetUrl);
  await flushAsyncWork();

  assert.deepEqual(harness.historyCalls, [targetUrl]);
  assert.equal(harness.location.href, targetUrl);
  assert.notEqual(harness.currentDocument._elements.get("page-shell").replacedBy, null);
});

test("document without authenticated shell marker hard navigates before side effects", async () => {
  const harness = createPartialNavHarness();
  const nextDocument = createDocument({ includeAuthenticatedMarker: false, extraScripts: {} });
  harness.queueResponse(harness.makeResponse(nextDocument));

  harness.navigate("https://example.com/manor/guests/");
  await flushAsyncWork();

  assert.equal(harness.location.href, "https://example.com/manor/guests/");
  assertNoPartialMutation(harness);
});

test("missing core section aborts atomically without replacing earlier sections", async () => {
  const harness = createPartialNavHarness();
  const nextDocument = createDocument({ missingSectionIds: ["info-bar"] });
  harness.queueResponse(harness.makeResponse(nextDocument));

  harness.navigate("https://example.com/manor/guests/");
  await flushAsyncWork();

  assert.equal(harness.location.href, "https://example.com/manor/guests/");
  assertNoPartialMutation(harness);
});

test("stale failed response cannot hard navigate over a newer navigation", async () => {
  const harness = createPartialNavHarness();
  let rejectFirstRequest;
  const firstResponse = new Promise((_resolve, reject) => {
    rejectFirstRequest = reject;
  });
  const secondDocument = createDocument();
  const secondResponse = harness.makeResponse(secondDocument);
  let requestCount = 0;
  harness.setFetchImplementation(() => {
    requestCount += 1;
    return requestCount === 1 ? firstResponse : secondResponse;
  });

  harness.navigate("https://example.com/manor/guests/");
  harness.navigate("https://example.com/manor/warehouse/");
  await flushAsyncWork();
  rejectFirstRequest(new Error("stale request failed"));
  await flushAsyncWork();

  assert.deepEqual(harness.historyCalls, ["https://example.com/manor/warehouse/"]);
  assert.equal(harness.location.href, "https://example.com/manor/warehouse/");
});

test("stale script execution cannot publish navigation after a newer navigation", async () => {
  let resolveFirstScripts;
  const firstScripts = new Promise((resolve) => {
    resolveFirstScripts = resolve;
  });
  let scriptRunCount = 0;
  const harness = createPartialNavHarness({
    async runPageScripts() {
      scriptRunCount += 1;
      if (scriptRunCount === 1) {
        await firstScripts;
      }
    },
  });
  const firstUrl = "https://example.com/manor/guests/";
  const secondUrl = "https://example.com/manor/warehouse/";
  harness.queueResponse(harness.makeResponse(createDocument({ extraScripts: {} })));
  harness.queueResponse(harness.makeResponse(createDocument({ extraScripts: {} })));

  harness.navigate(firstUrl);
  await flushAsyncWork();
  assert.equal(harness.scriptCalls.length, 1);

  harness.navigate(secondUrl);
  await flushAsyncWork();
  assert.deepEqual(harness.historyCalls, [secondUrl]);

  resolveFirstScripts();
  await flushAsyncWork();

  assert.deepEqual(harness.historyCalls, [secondUrl]);
  assert.equal(harness.location.href, secondUrl);
  assert.deepEqual(
    harness.currentDocument._events
      .filter((event) => event.type === "partial-nav:loaded")
      .map((event) => event.detail.url),
    [secondUrl],
  );
});

test("new navigation aborts the stale page script signal before it can add side effects", async () => {
  let resolveFirstScripts;
  const firstScripts = new Promise((resolve) => {
    resolveFirstScripts = resolve;
  });
  let firstSignal;
  let staleSideEffects = 0;
  let scriptRunCount = 0;
  const harness = createPartialNavHarness({
    async runPageScripts(options) {
      scriptRunCount += 1;
      if (scriptRunCount !== 1) return;
      firstSignal = options.signal;
      await firstScripts;
      if (!options.signal.aborted) {
        staleSideEffects += 1;
      }
    },
  });
  harness.queueResponse(harness.makeResponse(createDocument({ extraScripts: {} })));
  harness.queueResponse(harness.makeResponse(createDocument({ extraScripts: {} })));

  harness.navigate("https://example.com/manor/guests/");
  await flushAsyncWork();
  assert.ok(firstSignal);
  assert.equal(firstSignal.aborted, false);

  harness.navigate("https://example.com/manor/warehouse/");
  await flushAsyncWork();
  assert.equal(firstSignal.aborted, true);

  resolveFirstScripts();
  await flushAsyncWork();

  assert.equal(staleSideEffects, 0);
  assert.deepEqual(harness.historyCalls, ["https://example.com/manor/warehouse/"]);
});
