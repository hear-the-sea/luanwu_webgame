(function (root, factory) {
  const api = factory();

  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }

  root.PlayerFacingCopy = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const DEFAULT_BROWSER_ERROR_MESSAGE = "操作失败，请稍后重试";
  const ASCII_LETTER_RE = /[A-Za-z]/;
  const CHINESE_CHARACTER_RE = /[\u3400-\u9fff]/u;

  function browserErrorMessage(error, fallback = DEFAULT_BROWSER_ERROR_MESSAGE) {
    const fallbackText =
      typeof fallback === "string" && fallback.trim()
        ? fallback.trim()
        : DEFAULT_BROWSER_ERROR_MESSAGE;
    const rawMessage = error && typeof error.message === "string" ? error.message : error;
    if (typeof rawMessage !== "string") {
      return fallbackText;
    }

    const message = rawMessage.trim();
    if (!message || (ASCII_LETTER_RE.test(message) && !CHINESE_CHARACTER_RE.test(message))) {
      return fallbackText;
    }
    return message;
  }

  return {
    DEFAULT_BROWSER_ERROR_MESSAGE,
    browserErrorMessage,
  };
});
