(function (root, factory) {
  const api = factory(root.WorldChatWidgetCore);

  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }

  root.WorldChatWidgetRenderer = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (core) {
  "use strict";

  if (!core) {
    throw new Error("WorldChatWidgetCore is required before loading WorldChatWidgetRenderer");
  }

  function createRenderer(config) {
    const messagesEl = config.messagesEl;
    const userId = config.userId;
    const maxDomMessages = config.maxDomMessages;
    const messageTtlMs = config.messageTtlMs;
    const getIsOpen = config.getIsOpen;
    const setUnreadDot = config.setUnreadDot;
    const messageDeduplicator = core.createMessageDeduplicator();

    function shouldAutoScroll() {
      const threshold = 90;
      const distance = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight;
      return distance < threshold;
    }

    function trimDomMessages() {
      while (messagesEl.childElementCount > maxDomMessages) {
        messagesEl.removeChild(messagesEl.firstElementChild);
      }
    }

    function pruneOldMessages() {
      const cutoff = Date.now() - messageTtlMs;
      const children = Array.from(messagesEl.children);
      for (const el of children) {
        const ts = parseInt(el.dataset.ts || "0", 10);
        if (ts && ts < cutoff) {
          messagesEl.removeChild(el);
        }
      }
    }

    function formatTime(ts) {
      const date = new Date(typeof ts === "number" ? ts : Date.now());
      try {
        return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
      } catch (_error) {
        const hh = String(date.getHours()).padStart(2, "0");
        const mm = String(date.getMinutes()).padStart(2, "0");
        return `${hh}:${mm}`;
      }
    }

    function appendSystem(text, opts) {
      const shouldScroll = shouldAutoScroll();

      const line = document.createElement("div");
      line.className = "chat-line is-system";
      line.dataset.ts = String(Date.now());
      if (opts && opts.kind === "error") {
        line.classList.add("is-error");
      }

      const bubble = document.createElement("div");
      bubble.className = "chat-system";
      bubble.textContent = text || "";

      line.appendChild(bubble);
      messagesEl.appendChild(line);
      pruneOldMessages();
      trimDomMessages();

      if (shouldScroll) {
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }
    }

    function appendMessage(msg, opts) {
      const normalized = core.normalizeIncomingMessage(msg, userId);
      if (!normalized) {
        return;
      }

      if (!messageDeduplicator.accept(normalized)) return;

      const shouldScroll = shouldAutoScroll();
      const line = document.createElement("div");
      line.className = "chat-line";
      line.dataset.ts = String(normalized.timestamp);
      if (normalized.isSelf) {
        line.classList.add("is-self");
      }

      const bubbleWrap = document.createElement("div");
      bubbleWrap.className = "chat-bubble-wrap";

      const nameEl = document.createElement("span");
      nameEl.className = "chat-name";
      nameEl.textContent = normalized.senderName;

      const bubbleRow = document.createElement("div");
      bubbleRow.className = "chat-bubble-row";

      const bubble = document.createElement("div");
      bubble.className = "chat-bubble";
      bubble.textContent = normalized.text;

      const timeEl = document.createElement("span");
      timeEl.className = "chat-time";
      timeEl.textContent = formatTime(normalized.timestamp);

      bubbleRow.appendChild(bubble);
      bubbleRow.appendChild(timeEl);
      bubbleWrap.appendChild(nameEl);
      bubbleWrap.appendChild(bubbleRow);
      line.appendChild(bubbleWrap);

      messagesEl.appendChild(line);
      pruneOldMessages();
      trimDomMessages();

      if (shouldScroll) {
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }

      const fromHistory = Boolean(opts && opts.fromHistory);
      if (core.shouldMarkUnread({ isOpen: getIsOpen(), isSelf: normalized.isSelf, fromHistory })) {
        setUnreadDot(true);
      }
    }

    function clearMessages() {
      messagesEl.textContent = "";
      messageDeduplicator.clear();
    }

    function handlePayload(payload, setStatus) {
      if (!payload || typeof payload !== "object") {
        return;
      }

      if (payload.type === "history" && Array.isArray(payload.messages)) {
        clearMessages();
        payload.messages.forEach((message) => appendMessage(message, { fromHistory: true }));
        pruneOldMessages();
        messagesEl.scrollTop = messagesEl.scrollHeight;
        return;
      }

      if (payload.type === "status") {
        if (payload.status === "connected") {
          setStatus("已连接", "connected");
        }
        return;
      }

      if (payload.type === "error") {
        appendSystem(payload.message || "发生错误", { kind: "error" });
        return;
      }

      if (payload.type === "message") {
        appendMessage(payload);
      }
    }

    return {
      appendSystem,
      clearMessages,
      handlePayload,
      pruneOldMessages,
    };
  }

  return { createRenderer };
});
