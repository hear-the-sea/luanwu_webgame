const test = require("node:test");
const assert = require("node:assert/strict");

global.WorldChatWidgetCore = require("../chat_widget_core.js");
global.WebSocketReconnectPolicy = require("../websocket_reconnect.js");
const SERVICE_UNAVAILABLE_CLOSE_CODE = global.WebSocketReconnectPolicy.CLOSE_CODES.SERVICE_UNAVAILABLE;
const chatWidgetConnection = require("../chat_widget_connection.js");

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 3;
  static instances = [];

  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.CONNECTING;
    this.sent = [];
    FakeWebSocket.instances.push(this);
  }

  send(payload) {
    this.sent.push(payload);
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED;
    if (typeof this.onclose === "function") {
      this.onclose();
    }
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    if (typeof this.onopen === "function") {
      this.onopen();
    }
  }

  emitClose(code) {
    this.readyState = FakeWebSocket.CLOSED;
    if (typeof this.onclose === "function") {
      this.onclose({ code });
    }
  }

  emitMessage(payload) {
    if (typeof this.onmessage === "function") {
      this.onmessage({ data: JSON.stringify(payload) });
    }
  }
}

test("connection controller sends payloads after socket opens", () => {
  FakeWebSocket.instances = [];
  const statuses = [];
  const rendererCalls = [];
  let pingCallback = null;

  const controller = chatWidgetConnection.createConnectionController({
    WebSocketCtor: FakeWebSocket,
    renderer: {
      appendSystem() {
        rendererCalls.push("appendSystem");
      },
      handlePayload() {},
    },
    setStatus(label, state) {
      statuses.push({ label, state });
    },
    setIntervalFn(callback) {
      pingCallback = callback;
      return 1;
    },
    setTimeoutFn() {
      return 1;
    },
    clearTimeoutFn() {},
    clearIntervalFn() {},
    generateOperationId() {
      return "11111111-1111-4111-8111-111111111111";
    },
    wsUrl: "ws://example.com/ws/chat/world/",
  });

  controller.connect();
  assert.equal(FakeWebSocket.instances.length, 1);

  const socket = FakeWebSocket.instances[0];
  socket.open();

  const sent = controller.sendText("hello");
  assert.equal(sent, true);
  assert.deepEqual(
    socket.sent.map((entry) => JSON.parse(entry)),
    [
      {
        type: "send",
        text: "hello",
        operation_id: "11111111-1111-4111-8111-111111111111",
      },
    ]
  );

  pingCallback();
  assert.deepEqual(JSON.parse(socket.sent[1]), { type: "ping" });
  assert.deepEqual(statuses.slice(0, 2), [
    { label: "连接中", state: "connecting" },
    { label: "已连接", state: "connected" },
  ]);
  assert.deepEqual(rendererCalls, []);
});

test("connection controller reconnects after close and reports disconnected send attempts", () => {
  FakeWebSocket.instances = [];
  const statuses = [];
  const rendererMessages = [];
  let reconnectCallback = null;

  const controller = chatWidgetConnection.createConnectionController({
    WebSocketCtor: FakeWebSocket,
    renderer: {
      appendSystem(message) {
        rendererMessages.push(message);
      },
      handlePayload() {},
    },
    setStatus(label, state) {
      statuses.push({ label, state });
    },
    setTimeoutFn(callback) {
      reconnectCallback = callback;
      return 1;
    },
    clearTimeoutFn() {},
    setIntervalFn() {
      return 1;
    },
    clearIntervalFn() {},
    wsUrl: "ws://example.com/ws/chat/world/",
  });

  controller.connect();
  const socket = FakeWebSocket.instances[0];
  socket.emitClose();

  assert.equal(typeof reconnectCallback, "function");
  assert.deepEqual(statuses.slice(-2), [
    { label: "已断开", state: "disconnected" },
    { label: "重连中…", state: "connecting" },
  ]);

  const sent = controller.sendText("offline");
  assert.equal(sent, false);
  assert.deepEqual(rendererMessages, ["未连接到世界频道，正在重连…"]);

  reconnectCallback();
  assert.equal(FakeWebSocket.instances.length, 2);
});

test("connection controller resends pending text with the same operation id after reconnect", () => {
  FakeWebSocket.instances = [];
  let reconnectCallback = null;
  const controller = chatWidgetConnection.createConnectionController({
    WebSocketCtor: FakeWebSocket,
    renderer: { appendSystem() {}, handlePayload() {} },
    setStatus() {},
    setTimeoutFn(callback) {
      reconnectCallback = callback;
      return 1;
    },
    clearTimeoutFn() {},
    setIntervalFn() {
      return 1;
    },
    clearIntervalFn() {},
    generateOperationId() {
      return "22222222-2222-4222-8222-222222222222";
    },
    userId: 7,
    wsUrl: "ws://example.com/ws/chat/world/",
  });

  controller.connect();
  const firstSocket = FakeWebSocket.instances[0];
  firstSocket.open();
  assert.equal(controller.sendText("retry me"), true);
  const firstPayload = JSON.parse(firstSocket.sent[0]);

  firstSocket.emitClose();
  reconnectCallback();
  const secondSocket = FakeWebSocket.instances[1];
  secondSocket.open();

  assert.deepEqual(JSON.parse(secondSocket.sent[0]), firstPayload);
  assert.equal(firstPayload.operation_id, "22222222-2222-4222-8222-222222222222");
});

test("matching ack broadcast and explicit error clear pending operations", () => {
  FakeWebSocket.instances = [];
  let reconnectCallback = null;
  const expectedOperationIds = [
    "33333333-3333-4333-8333-333333333331",
    "33333333-3333-4333-8333-333333333332",
    "33333333-3333-4333-8333-333333333333",
  ];
  const operationIds = [...expectedOperationIds];
  const controller = chatWidgetConnection.createConnectionController({
    WebSocketCtor: FakeWebSocket,
    renderer: { appendSystem() {}, handlePayload() {} },
    setStatus() {},
    setTimeoutFn(callback) {
      reconnectCallback = callback;
      return 1;
    },
    clearTimeoutFn() {},
    setIntervalFn() {
      return 1;
    },
    clearIntervalFn() {},
    generateOperationId() {
      return operationIds.shift();
    },
    userId: 7,
    wsUrl: "ws://example.com/ws/chat/world/",
  });

  controller.connect();
  const firstSocket = FakeWebSocket.instances[0];
  firstSocket.open();
  controller.sendText("acked");
  controller.sendText("broadcast");
  controller.sendText("rejected");
  const sent = firstSocket.sent.map((entry) => JSON.parse(entry));
  assert.deepEqual(
    sent.map((payload) => payload.operation_id),
    expectedOperationIds
  );

  firstSocket.emitMessage({ type: "send_ack", operation_id: sent[0].operation_id, status: "queued" });
  firstSocket.emitMessage({
    type: "message",
    operation_id: sent[1].operation_id,
    id: "different",
    sender: { id: 7 },
  });
  firstSocket.emitMessage({ type: "error", operation_id: sent[2].operation_id, code: "no_trumpet" });
  firstSocket.emitClose();
  reconnectCallback();
  const secondSocket = FakeWebSocket.instances[1];
  secondSocket.open();

  assert.deepEqual(secondSocket.sent, []);
});

test("another sender with the same operation id does not clear pending", () => {
  FakeWebSocket.instances = [];
  let reconnectCallback = null;
  const controller = chatWidgetConnection.createConnectionController({
    WebSocketCtor: FakeWebSocket,
    renderer: { appendSystem() {}, handlePayload() {} },
    setStatus() {},
    setTimeoutFn(callback) {
      reconnectCallback = callback;
      return 1;
    },
    clearTimeoutFn() {},
    setIntervalFn() {
      return 1;
    },
    clearIntervalFn() {},
    generateOperationId() {
      return "44444444-4444-4444-8444-444444444444";
    },
    userId: 7,
    wsUrl: "ws://example.com/ws/chat/world/",
  });

  controller.connect();
  const firstSocket = FakeWebSocket.instances[0];
  firstSocket.open();
  controller.sendText("mine");
  const sent = JSON.parse(firstSocket.sent[0]);
  firstSocket.emitMessage({
    type: "message",
    operation_id: sent.operation_id,
    sender: { id: 8 },
  });
  firstSocket.emitClose();
  reconnectCallback();
  const secondSocket = FakeWebSocket.instances[1];
  secondSocket.open();

  assert.deepEqual(JSON.parse(secondSocket.sent[0]), sent);
});

test("reconnects and preserves pending when the first resend throws", () => {
  FakeWebSocket.instances = [];
  const reconnectCallbacks = [];
  const controller = chatWidgetConnection.createConnectionController({
    WebSocketCtor: FakeWebSocket,
    renderer: { appendSystem() {}, handlePayload() {} },
    setStatus() {},
    setTimeoutFn(callback, delay) {
      if (delay !== 30000) reconnectCallbacks.push(callback);
      return Symbol("timeout");
    },
    clearTimeoutFn() {},
    setIntervalFn() {
      return 1;
    },
    clearIntervalFn() {},
    generateOperationId() {
      return "55555555-5555-4555-8555-555555555555";
    },
    userId: 7,
    wsUrl: "ws://example.com/ws/chat/world/",
  });

  controller.connect();
  const firstSocket = FakeWebSocket.instances[0];
  firstSocket.open();
  controller.sendText("retry first");
  const originalPayload = JSON.parse(firstSocket.sent[0]);
  firstSocket.emitClose();
  reconnectCallbacks[0]();

  const secondSocket = FakeWebSocket.instances[1];
  secondSocket.send = () => {
    throw new Error("resend failed");
  };
  secondSocket.open();

  assert.equal(secondSocket.readyState, FakeWebSocket.CLOSED);
  assert.equal(reconnectCallbacks.length, 2);
  reconnectCallbacks[1]();
  const thirdSocket = FakeWebSocket.instances[2];
  thirdSocket.open();
  assert.deepEqual(JSON.parse(thirdSocket.sent[0]), originalPayload);
});

test("reconnects and preserves every pending operation when a middle resend throws", () => {
  FakeWebSocket.instances = [];
  const reconnectCallbacks = [];
  const operationIds = [
    "66666666-6666-4666-8666-666666666661",
    "66666666-6666-4666-8666-666666666662",
  ];
  const controller = chatWidgetConnection.createConnectionController({
    WebSocketCtor: FakeWebSocket,
    renderer: { appendSystem() {}, handlePayload() {} },
    setStatus() {},
    setTimeoutFn(callback, delay) {
      if (delay !== 30000) reconnectCallbacks.push(callback);
      return Symbol("timeout");
    },
    clearTimeoutFn() {},
    setIntervalFn() {
      return 1;
    },
    clearIntervalFn() {},
    generateOperationId() {
      return operationIds.shift();
    },
    userId: 7,
    wsUrl: "ws://example.com/ws/chat/world/",
  });

  controller.connect();
  const firstSocket = FakeWebSocket.instances[0];
  firstSocket.open();
  controller.sendText("first pending");
  controller.sendText("second pending");
  const originalPayloads = firstSocket.sent.map((entry) => JSON.parse(entry));
  firstSocket.emitClose();
  reconnectCallbacks[0]();

  const secondSocket = FakeWebSocket.instances[1];
  let resendCount = 0;
  secondSocket.send = (payload) => {
    resendCount += 1;
    if (resendCount === 2) throw new Error("middle resend failed");
    secondSocket.sent.push(payload);
  };
  secondSocket.open();

  assert.equal(secondSocket.readyState, FakeWebSocket.CLOSED);
  reconnectCallbacks[1]();
  const thirdSocket = FakeWebSocket.instances[2];
  thirdSocket.open();
  assert.deepEqual(
    thirdSocket.sent.map((entry) => JSON.parse(entry)),
    originalPayloads
  );
});

test("schedules reconnect when closing a failed resend socket throws", () => {
  FakeWebSocket.instances = [];
  const reconnectCallbacks = [];
  const controller = chatWidgetConnection.createConnectionController({
    WebSocketCtor: FakeWebSocket,
    renderer: { appendSystem() {}, handlePayload() {} },
    setStatus() {},
    setTimeoutFn(callback, delay) {
      if (delay !== 30000) reconnectCallbacks.push(callback);
      return Symbol("timeout");
    },
    clearTimeoutFn() {},
    setIntervalFn() {
      return 1;
    },
    clearIntervalFn() {},
    generateOperationId() {
      return "77777777-7777-4777-8777-777777777777";
    },
    userId: 7,
    wsUrl: "ws://example.com/ws/chat/world/",
  });

  controller.connect();
  const firstSocket = FakeWebSocket.instances[0];
  firstSocket.open();
  controller.sendText("close throws");
  const originalPayload = JSON.parse(firstSocket.sent[0]);
  firstSocket.emitClose();
  reconnectCallbacks[0]();

  const secondSocket = FakeWebSocket.instances[1];
  secondSocket.send = () => {
    throw new Error("resend failed");
  };
  secondSocket.close = () => {
    throw new Error("close failed");
  };
  secondSocket.open();

  assert.equal(reconnectCallbacks.length, 2);
  reconnectCallbacks[1]();
  const thirdSocket = FakeWebSocket.instances[2];
  thirdSocket.open();
  assert.deepEqual(JSON.parse(thirdSocket.sent[0]), originalPayload);
});

function createLifecycleHarness({ random = 0.5 } = {}) {
  FakeWebSocket.instances = [];
  const timeouts = [];
  const intervals = [];
  let nextTimerId = 1;

  function addTimer(collection, callback, delay) {
    const timer = { id: nextTimerId, callback, delay, active: true };
    nextTimerId += 1;
    collection.push(timer);
    return timer.id;
  }

  const controller = chatWidgetConnection.createConnectionController({
    WebSocketCtor: FakeWebSocket,
    renderer: { appendSystem() {}, handlePayload() {} },
    reconnectPolicyApi: {
      createReconnectPolicy(options = {}) {
        return global.WebSocketReconnectPolicy.createReconnectPolicy({
          ...options,
          randomFn: () => random,
        });
      },
    },
    setStatus() {},
    setTimeoutFn(callback, delay) {
      return addTimer(timeouts, callback, delay);
    },
    clearTimeoutFn(timerId) {
      const timer = timeouts.find((candidate) => candidate.id === timerId);
      if (timer) timer.active = false;
    },
    setIntervalFn(callback, delay) {
      return addTimer(intervals, callback, delay);
    },
    clearIntervalFn(timerId) {
      const timer = intervals.find((candidate) => candidate.id === timerId);
      if (timer) timer.active = false;
    },
    wsUrl: "ws://example.com/ws/chat/world/",
  });

  return {
    controller,
    intervals,
    sockets: FakeWebSocket.instances,
    activeTimeouts() {
      return timeouts.filter((timer) => timer.active);
    },
    reconnectTimers() {
      return timeouts.filter((timer) => timer.active && timer.delay !== 30000);
    },
    runTimer(timer) {
      assert.equal(timer.active, true);
      timer.active = false;
      timer.callback();
    },
  };
}

test("terminal authentication close codes stop reconnecting", () => {
  for (const code of [4401, 4403]) {
    const harness = createLifecycleHarness();
    harness.controller.connect();

    harness.sockets[0].emitClose(code);

    assert.equal(harness.activeTimeouts().length, 0);
    harness.controller.connect();
    assert.equal(harness.controller.sendText("blocked"), false);
    assert.equal(harness.sockets.length, 1);
  }
});

test("capacity close schedules one short reconnect", () => {
  const harness = createLifecycleHarness({ random: 0 });
  harness.controller.connect();
  const socket = harness.sockets[0];
  socket.open();

  socket.emitClose(4429);
  socket.onclose({ code: 4429 });

  assert.deepEqual(harness.reconnectTimers().map((timer) => timer.delay), [1000]);
});

test("opening a chat socket does not reset transient backoff", () => {
  const harness = createLifecycleHarness();
  harness.controller.connect();
  harness.sockets[0].open();
  harness.sockets[0].emitClose(SERVICE_UNAVAILABLE_CLOSE_CODE);
  assert.equal(harness.reconnectTimers()[0].delay, 2000);
  harness.runTimer(harness.reconnectTimers()[0]);

  harness.sockets[1].open();
  harness.sockets[1].emitClose(SERVICE_UNAVAILABLE_CLOSE_CODE);

  assert.equal(harness.reconnectTimers()[0].delay, 4000);
});

test("valid chat messages reset transient backoff", () => {
  const harness = createLifecycleHarness();
  harness.controller.connect();
  harness.sockets[0].emitClose(SERVICE_UNAVAILABLE_CLOSE_CODE);
  harness.runTimer(harness.reconnectTimers()[0]);
  harness.sockets[1].emitClose(SERVICE_UNAVAILABLE_CLOSE_CODE);
  harness.runTimer(harness.reconnectTimers()[0]);

  const socket = harness.sockets[2];
  socket.open();
  socket.emitMessage({ type: "message", id: "valid" });
  socket.emitClose(SERVICE_UNAVAILABLE_CLOSE_CODE);

  assert.equal(harness.reconnectTimers()[0].delay, 2000);
});

test("thirty stable seconds reset chat transient backoff", () => {
  const harness = createLifecycleHarness();
  harness.controller.connect();
  harness.sockets[0].emitClose(SERVICE_UNAVAILABLE_CLOSE_CODE);
  harness.runTimer(harness.reconnectTimers()[0]);

  const socket = harness.sockets[1];
  socket.open();
  const stabilityTimer = harness.activeTimeouts().find((timer) => timer.delay === 30000);
  harness.runTimer(stabilityTimer);
  socket.emitClose(SERVICE_UNAVAILABLE_CLOSE_CODE);

  assert.equal(harness.reconnectTimers()[0].delay, 2000);
});

test("teardown clears reconnect ping and stability timers", () => {
  const harness = createLifecycleHarness();
  harness.controller.connect();
  const socket = harness.sockets[0];
  socket.open();

  harness.controller.teardown();

  assert.equal(harness.activeTimeouts().length, 0);
  assert.equal(harness.intervals.filter((timer) => timer.active).length, 0);
  assert.equal(socket.readyState, FakeWebSocket.CLOSED);
});
