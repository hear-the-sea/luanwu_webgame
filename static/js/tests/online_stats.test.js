const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const reconnectPolicyApi = require("../websocket_reconnect.js");
const SERVICE_UNAVAILABLE_CLOSE_CODE = reconnectPolicyApi.CLOSE_CODES.SERVICE_UNAVAILABLE;

function createOnlineStatsHarness({ random = 0.5 } = {}) {
  const scriptPath = path.resolve(__dirname, "..", "online_stats.js");
  const scriptSource = fs.readFileSync(scriptPath, "utf8");
  const timers = [];
  const windowListeners = new Map();
  const elements = {
    "online-user-count": { textContent: "0" },
    "total-user-count": { textContent: "0" },
  };
  let nextTimerId = 1;

  function setFakeTimeout(fn, delay = 0) {
    const timer = { id: nextTimerId, fn, delay, active: true };
    nextTimerId += 1;
    timers.push(timer);
    return timer.id;
  }

  function clearFakeTimeout(timerId) {
    const timer = timers.find((candidate) => candidate.id === timerId);
    if (timer) timer.active = false;
  }

  class FakeWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 3;
    static instances = [];

    constructor(url) {
      this.url = url;
      this.readyState = FakeWebSocket.CONNECTING;
      this.closeCalls = 0;
      FakeWebSocket.instances.push(this);
    }

    open() {
      this.readyState = FakeWebSocket.OPEN;
      this.onopen({});
    }

    message(payload) {
      this.onmessage({ data: JSON.stringify(payload) });
    }

    closeWith(code) {
      this.readyState = FakeWebSocket.CLOSED;
      this.onclose({ code });
    }

    close() {
      this.closeCalls += 1;
      this.readyState = FakeWebSocket.CLOSED;
    }
  }

  const documentObj = {
    readyState: "complete",
    getElementById(id) {
      return elements[id] || null;
    },
    addEventListener() {},
  };
  const consoleObj = {
    errorCalls: [],
    logCalls: [],
    error(...args) {
      this.errorCalls.push(args);
    },
    log(...args) {
      this.logCalls.push(args);
    },
  };
  const windowObj = {
    WebSocket: FakeWebSocket,
    WebSocketReconnectPolicy: {
      createReconnectPolicy(options = {}) {
        return reconnectPolicyApi.createReconnectPolicy({ ...options, randomFn: () => random });
      },
    },
    location: { protocol: "http:", host: "example.test" },
    addEventListener(type, listener) {
      windowListeners.set(type, listener);
    },
    setTimeout: setFakeTimeout,
    clearTimeout: clearFakeTimeout,
  };
  windowObj.window = windowObj;
  windowObj.document = documentObj;

  const context = {
    window: windowObj,
    document: documentObj,
    console: consoleObj,
    WebSocket: FakeWebSocket,
    setTimeout: setFakeTimeout,
    clearTimeout: clearFakeTimeout,
  };
  vm.createContext(context);
  vm.runInContext(scriptSource, context, { filename: "online_stats.js" });

  return {
    consoleObj,
    elements,
    sockets: FakeWebSocket.instances,
    activeTimers() {
      return timers.filter((timer) => timer.active);
    },
    reconnectTimers() {
      return timers.filter((timer) => timer.active && timer.delay !== 30000);
    },
    runTimer(timer) {
      assert.equal(timer.active, true);
      timer.active = false;
      timer.fn();
    },
    pagehide() {
      const listener = windowListeners.get("pagehide");
      if (listener) listener({ persisted: true });
    },
    pageshow() {
      const listener = windowListeners.get("pageshow");
      if (listener) listener({ persisted: true });
    },
  };
}

test("terminal authentication closes do not reconnect", () => {
  for (const code of [4401, 4403]) {
    const harness = createOnlineStatsHarness();

    harness.sockets[0].closeWith(code);

    assert.equal(harness.activeTimers().length, 0);
    assert.equal(harness.sockets.length, 1);
  }
});

test("accepted then capacity closed schedules one short retry", () => {
  const harness = createOnlineStatsHarness({ random: 0 });
  const first = harness.sockets[0];
  first.open();

  first.closeWith(4429);
  first.onclose({ code: 4429 });

  assert.deepEqual(harness.reconnectTimers().map((timer) => timer.delay), [1000]);
});

test("opening a new socket does not reset transient backoff", () => {
  const harness = createOnlineStatsHarness({ random: 0.5 });

  harness.sockets[0].open();
  harness.sockets[0].closeWith(SERVICE_UNAVAILABLE_CLOSE_CODE);
  assert.equal(harness.reconnectTimers()[0].delay, 2000);
  harness.runTimer(harness.reconnectTimers()[0]);

  harness.sockets[1].open();
  harness.sockets[1].closeWith(SERVICE_UNAVAILABLE_CLOSE_CODE);
  assert.equal(harness.reconnectTimers()[0].delay, 4000);
});

test("abnormal closes retry within two seconds", () => {
  const harness = createOnlineStatsHarness({ random: 1 });

  harness.sockets[0].closeWith(1006);

  assert.equal(harness.reconnectTimers()[0].delay, 2000);
});

test("reconnect attempts log the disconnected transition only once", () => {
  const harness = createOnlineStatsHarness();

  harness.sockets[0].closeWith(1006);
  harness.runTimer(harness.reconnectTimers()[0]);
  harness.sockets[1].open();
  harness.sockets[1].closeWith(1006);

  const disconnectLogs = harness.consoleObj.logCalls.filter((args) =>
    String(args[0]).includes("已断开"),
  );
  assert.equal(disconnectLogs.length, 1);
});

test("valid statistics update the DOM and reset transient backoff", () => {
  const harness = createOnlineStatsHarness({ random: 0.5 });

  harness.sockets[0].closeWith(SERVICE_UNAVAILABLE_CLOSE_CODE);
  harness.runTimer(harness.reconnectTimers()[0]);
  harness.sockets[1].closeWith(SERVICE_UNAVAILABLE_CLOSE_CODE);
  harness.runTimer(harness.reconnectTimers()[0]);

  const socket = harness.sockets[2];
  socket.open();
  socket.message({ online_count: 7, total_count: 19 });
  socket.closeWith(SERVICE_UNAVAILABLE_CLOSE_CODE);

  assert.equal(harness.elements["online-user-count"].textContent, 7);
  assert.equal(harness.elements["total-user-count"].textContent, 19);
  assert.equal(harness.reconnectTimers()[0].delay, 2000);
});

test("page suspension closes the socket and restores exactly one connection", () => {
  const harness = createOnlineStatsHarness();
  const socket = harness.sockets[0];
  socket.open();

  harness.pagehide();

  assert.equal(socket.closeCalls, 1);
  assert.equal(harness.activeTimers().length, 0);

  harness.pageshow();
  harness.pageshow();

  assert.equal(harness.sockets.length, 2);
});
