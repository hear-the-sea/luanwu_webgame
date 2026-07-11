(function (root, factory) {
  const api = factory();

  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }

  root.WorldChatWidgetCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const DEFAULT_MAX_MESSAGE_IDS = 400;

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function buildWebSocketUrl(locationLike, wsPath) {
    const path = wsPath || "/ws/chat/world/";
    const protocol = locationLike && locationLike.protocol === "https:" ? "wss" : "ws";
    const host = locationLike && locationLike.host ? String(locationLike.host) : "";
    return `${protocol}://${host}${path}`;
  }

  function normalizeOutgoingText(rawValue) {
    if (rawValue == null) {
      return "";
    }
    return String(rawValue).replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
  }

  function parseStoredPosition(rawValue) {
    if (!rawValue) {
      return null;
    }

    let parsed;
    try {
      parsed = typeof rawValue === "string" ? JSON.parse(rawValue) : rawValue;
    } catch (_error) {
      return null;
    }

    const left = typeof parsed.left === "number" ? parsed.left : parseFloat(parsed.left);
    const top = typeof parsed.top === "number" ? parsed.top : parseFloat(parsed.top);
    if (!Number.isFinite(left) || !Number.isFinite(top)) {
      return null;
    }

    return { left, top };
  }

  function serializeStoredPosition(rectLike) {
    const left = rectLike && typeof rectLike.left === "number" ? rectLike.left : parseFloat(rectLike.left);
    const top = rectLike && typeof rectLike.top === "number" ? rectLike.top : parseFloat(rectLike.top);
    if (!Number.isFinite(left) || !Number.isFinite(top)) {
      return null;
    }

    return JSON.stringify({ left: Math.round(left), top: Math.round(top) });
  }

  function nextReconnectDelay(currentDelay) {
    const baseDelay = Number.isFinite(currentDelay) && currentDelay > 0 ? currentDelay : 1200;
    return Math.min(Math.floor(baseDelay * 1.6), 15000);
  }

  function generateOperationId(cryptoLike, randomFn) {
    const cryptoSource =
      cryptoLike === undefined && typeof globalThis !== "undefined" ? globalThis.crypto : cryptoLike;
    if (cryptoSource && typeof cryptoSource.randomUUID === "function") {
      return cryptoSource.randomUUID();
    }

    const bytes = new Uint8Array(16);
    if (cryptoSource && typeof cryptoSource.getRandomValues === "function") {
      cryptoSource.getRandomValues(bytes);
    } else {
      const random = typeof randomFn === "function" ? randomFn : Math.random;
      for (let index = 0; index < bytes.length; index += 1) {
        bytes[index] = Math.floor(random() * 256) & 0xff;
      }
    }
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0"));
    return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex
      .slice(6, 8)
      .join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
  }

  function messageDedupKey(message) {
    if (!message || typeof message !== "object") {
      return "";
    }
    const operationIdValue =
      message.operation_id != null ? message.operation_id : message.operationId;
    const operationId = operationIdValue != null ? String(operationIdValue) : "";
    if (operationId) {
      const sender = message.sender && typeof message.sender === "object" ? message.sender : {};
      const senderIdValue = sender.id != null ? sender.id : message.senderId;
      const senderId = senderIdValue != null ? String(senderIdValue) : "";
      return `operation:${senderId}:${operationId}`;
    }
    const messageIdValue = message.id != null ? message.id : message.msgId;
    const messageId = messageIdValue != null ? String(messageIdValue) : "";
    return messageId ? `id:${messageId}` : "";
  }

  function createMessageDeduplicator(maxSize) {
    const keys = new Set();

    function accept(message) {
      const key = messageDedupKey(message);
      if (!key) return true;
      if (keys.has(key)) return false;

      keys.add(key);
      if (shouldResetMessageIds(keys.size, maxSize)) {
        keys.clear();
        keys.add(key);
      }
      return true;
    }

    function clear() {
      keys.clear();
    }

    return { accept, clear };
  }

  function normalizeIncomingMessage(msg, userId) {
    if (!msg || typeof msg !== "object" || msg.type !== "message") {
      return null;
    }

    const sender = msg.sender && typeof msg.sender === "object" ? msg.sender : {};
    const senderIdValue =
      typeof sender.id === "number" ? sender.id : parseInt(sender.id || "0", 10);
    const senderId = Number.isFinite(senderIdValue) ? senderIdValue : 0;
    const normalizedUserId =
      typeof userId === "number" ? userId : parseInt(userId || "0", 10);
    const safeUserId = Number.isFinite(normalizedUserId) ? normalizedUserId : 0;

    const msgId = msg.id != null ? String(msg.id) : "";
    const operationId = msg.operation_id != null ? String(msg.operation_id) : "";
    return {
      dedupKey: messageDedupKey(msg),
      msgId,
      operationId,
      senderId,
      senderName: sender.name ? String(sender.name) : "玩家",
      text: msg.text != null ? String(msg.text) : "",
      timestamp: typeof msg.ts === "number" ? msg.ts : Date.now(),
      isSelf: Boolean(safeUserId && senderId && safeUserId === senderId),
    };
  }

  function shouldResetMessageIds(size, maxSize) {
    const limit = Number.isFinite(maxSize) && maxSize > 0 ? maxSize : DEFAULT_MAX_MESSAGE_IDS;
    return size > limit;
  }

  function shouldMarkUnread(options) {
    return Boolean(options && !options.isOpen && !options.isSelf && !options.fromHistory);
  }

  return {
    DEFAULT_MAX_MESSAGE_IDS,
    buildWebSocketUrl,
    clamp,
    createMessageDeduplicator,
    generateOperationId,
    messageDedupKey,
    nextReconnectDelay,
    normalizeIncomingMessage,
    normalizeOutgoingText,
    parseStoredPosition,
    serializeStoredPosition,
    shouldMarkUnread,
    shouldResetMessageIds,
  };
});
