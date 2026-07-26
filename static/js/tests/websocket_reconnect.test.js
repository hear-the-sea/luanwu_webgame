const test = require("node:test");
const assert = require("node:assert/strict");

const policyApi = require("../websocket_reconnect.js");
const SERVICE_UNAVAILABLE_CLOSE_CODE = policyApi.CLOSE_CODES.SERVICE_UNAVAILABLE;

test("shared reconnect close-code contract stays synchronized", () => {
  assert.deepEqual(policyApi.CLOSE_CODES, {
    AUTHENTICATION_REQUIRED: 4401,
    INVALID_SESSION: 4403,
    CAPACITY_LIMIT_REACHED: 4429,
    SERVICE_UNAVAILABLE: 4503,
    ABNORMAL_CLOSURE: 1006,
  });
});

test("authentication close codes are terminal", () => {
  const policy = policyApi.createReconnectPolicy({ randomFn: () => 0.5 });

  assert.equal(policy.shouldReconnect(4401), false);
  assert.equal(policy.shouldReconnect(4403), false);
  assert.equal(policy.shouldReconnect(1006), true);
  assert.equal(policy.shouldReconnect(SERVICE_UNAVAILABLE_CLOSE_CODE), true);
  assert.equal(policy.shouldReconnect(4429), true);
});

test("capacity retries stay between one and two seconds", () => {
  const earliest = policyApi.createReconnectPolicy({ randomFn: () => 0 });
  const latest = policyApi.createReconnectPolicy({ randomFn: () => 1 });

  assert.equal(earliest.nextDelay(4429), 1000);
  assert.equal(latest.nextDelay(4429), 2000);
});

test("abnormal closures use the short recovery window", () => {
  const earliest = policyApi.createReconnectPolicy({ randomFn: () => 0 });
  const latest = policyApi.createReconnectPolicy({ randomFn: () => 1 });

  assert.equal(earliest.nextDelay(1006), 1000);
  assert.equal(latest.nextDelay(1006), 2000);
});

test("abnormal retry attempts cover the worker lease expiry window", () => {
  const policy = policyApi.createReconnectPolicy({ randomFn: () => 1 });
  let elapsed = 0;
  const attemptTimes = [];

  for (let attempt = 0; attempt < 5; attempt += 1) {
    elapsed += policy.nextDelay(1006);
    attemptTimes.push(elapsed);
  }

  assert.deepEqual(attemptTimes, [2000, 4000, 6000, 8000, 10000]);
});

test("capacity and abnormal closures use only five fast retries before exponential backoff", () => {
  for (const closeCode of [1006, 4429]) {
    const policy = policyApi.createReconnectPolicy({ randomFn: () => 0.5 });

    assert.deepEqual(
      Array.from({ length: 8 }, () => policy.nextDelay(closeCode)),
      [1500, 1500, 1500, 1500, 1500, 2000, 4000, 8000],
    );
  }
});

test("capacity and abnormal closures share one fast retry budget", () => {
  const policy = policyApi.createReconnectPolicy({ randomFn: () => 0.5 });

  assert.deepEqual(
    [1006, 4429, 1006, 4429, 1006, 4429].map((closeCode) => policy.nextDelay(closeCode)),
    [1500, 1500, 1500, 1500, 1500, 2000],
  );
});

test("transient delay grows exponentially and stays capped", () => {
  const policy = policyApi.createReconnectPolicy({ randomFn: () => 0.5 });

  assert.deepEqual(
    [
      policy.nextDelay(SERVICE_UNAVAILABLE_CLOSE_CODE),
      policy.nextDelay(SERVICE_UNAVAILABLE_CLOSE_CODE),
      policy.nextDelay(SERVICE_UNAVAILABLE_CLOSE_CODE),
      policy.nextDelay(SERVICE_UNAVAILABLE_CLOSE_CODE),
      policy.nextDelay(SERVICE_UNAVAILABLE_CLOSE_CODE),
    ],
    [2000, 4000, 8000, 15000, 15000],
  );
});

test("positive transient jitter does not exceed the delay cap", () => {
  const policy = policyApi.createReconnectPolicy({ randomFn: () => 1 });

  assert.deepEqual(
    [
      policy.nextDelay(SERVICE_UNAVAILABLE_CLOSE_CODE),
      policy.nextDelay(SERVICE_UNAVAILABLE_CLOSE_CODE),
      policy.nextDelay(SERVICE_UNAVAILABLE_CLOSE_CODE),
      policy.nextDelay(SERVICE_UNAVAILABLE_CLOSE_CODE),
      policy.nextDelay(SERVICE_UNAVAILABLE_CLOSE_CODE),
    ],
    [2200, 4400, 8800, 15000, 15000],
  );
});

test("markStable resets both the fast retry budget and exponential delay", () => {
  const policy = policyApi.createReconnectPolicy({ randomFn: () => 0.5 });
  const expectedCycle = [1500, 1500, 1500, 1500, 1500, 2000, 4000];

  assert.deepEqual(
    Array.from({ length: 7 }, () => policy.nextDelay(1006)),
    expectedCycle,
  );

  policy.markStable();

  assert.deepEqual(
    Array.from({ length: 7 }, () => policy.nextDelay(4429)),
    expectedCycle,
  );
});

test("transient delay resets only when marked stable", () => {
  const policy = policyApi.createReconnectPolicy({ randomFn: () => 0.5 });

  assert.deepEqual(
    [policy.nextDelay(SERVICE_UNAVAILABLE_CLOSE_CODE), policy.nextDelay(SERVICE_UNAVAILABLE_CLOSE_CODE)],
    [2000, 4000],
  );
  policy.markStable();
  assert.equal(policy.nextDelay(SERVICE_UNAVAILABLE_CLOSE_CODE), 2000);
});
