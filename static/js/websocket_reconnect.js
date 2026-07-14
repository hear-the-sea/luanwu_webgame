(function (root, factory) {
  const api = factory();

  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }

  root.WebSocketReconnectPolicy = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const TERMINAL_CLOSE_CODES = new Set([4401, 4403]);
  const CAPACITY_CLOSE_CODE = 4429;
  const ABNORMAL_CLOSE_CODE = 1006;
  const FAST_RETRY_LIMIT = 5;
  const DEFAULT_TRANSIENT_BASE_MS = 2000;
  const DEFAULT_TRANSIENT_MAX_MS = 15000;

  function normalizePositiveNumber(value, fallback) {
    return Number.isFinite(value) && value > 0 ? value : fallback;
  }

  function createReconnectPolicy(options = {}) {
    const randomFn = typeof options.randomFn === "function" ? options.randomFn : Math.random;
    const transientBaseMs = normalizePositiveNumber(
      options.transientBaseMs,
      DEFAULT_TRANSIENT_BASE_MS,
    );
    const transientMaxMs = Math.max(
      transientBaseMs,
      normalizePositiveNumber(options.transientMaxMs, DEFAULT_TRANSIENT_MAX_MS),
    );
    let transientDelayMs = transientBaseMs;
    let fastRetryCount = 0;

    function randomUnit() {
      return Math.min(1, Math.max(0, Number(randomFn()) || 0));
    }

    function shouldReconnect(closeCode) {
      return !TERMINAL_CLOSE_CODES.has(Number(closeCode));
    }

    function nextDelay(closeCode) {
      if (
        [CAPACITY_CLOSE_CODE, ABNORMAL_CLOSE_CODE].includes(Number(closeCode)) &&
        fastRetryCount < FAST_RETRY_LIMIT
      ) {
        fastRetryCount += 1;
        return Math.round(1000 + randomUnit() * 1000);
      }

      const delay = Math.min(
        transientMaxMs,
        Math.round(transientDelayMs * (0.9 + randomUnit() * 0.2)),
      );
      transientDelayMs = Math.min(transientDelayMs * 2, transientMaxMs);
      return delay;
    }

    function markStable() {
      transientDelayMs = transientBaseMs;
      fastRetryCount = 0;
    }

    return {
      markStable,
      nextDelay,
      shouldReconnect,
    };
  }

  return {
    createReconnectPolicy,
  };
});
