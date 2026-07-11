(function () {
  "use strict";

  const core = window.WorldChatWidgetCore;
  const connectionApi = window.WorldChatWidgetConnection;
  const layoutApi = window.WorldChatWidgetLayout;
  const rendererApi = window.WorldChatWidgetRenderer;
  const widget = document.getElementById("chat-widget");
  if (!widget) return;

  if (!core || !rendererApi || !layoutApi || !connectionApi) {
    return;
  }

  if (!window.WebSocket) {
    return;
  }

  const userId = parseInt(widget.dataset.userId || "0", 10);
  const wsPath = widget.dataset.wsPath || "/ws/chat/world/";
  const wsUrl = core.buildWebSocketUrl(window.location, wsPath);

  const fab = document.getElementById("chat-fab");
  const badge = document.getElementById("chat-fab-badge");
  const panel = document.getElementById("chat-panel");
  const statusEl = document.getElementById("chat-status");
  const messagesEl = document.getElementById("chat-messages");
  const inputEl = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send");
  const closeBtn = document.getElementById("chat-close");
  const clearBtn = document.getElementById("chat-clear");
  const headerEl = panel ? panel.querySelector(".chat-header") : null;

  if (!fab || !panel || !messagesEl || !inputEl || !sendBtn || !closeBtn || !clearBtn || !headerEl) return;

  const STORAGE_OPEN_KEY = "chat:world:open";
  const STORAGE_POS_KEY = "chat:world:pos";
  const STORAGE_UNREAD_KEY = "chat:world:unread";
  const MAX_DOM_MESSAGES = 260;
  const MESSAGE_TTL_MS = 24 * 60 * 60 * 1000;
  let pruneTimer = null;

  let isOpen = false;
  let hasUnread = false;
  const renderer = rendererApi.createRenderer({
    getIsOpen: () => isOpen,
    maxDomMessages: MAX_DOM_MESSAGES,
    messageTtlMs: MESSAGE_TTL_MS,
    messagesEl,
    setUnreadDot,
    userId,
  });
  const layoutController = layoutApi.createLayoutController({
    core,
    fab,
    getIsOpen: () => isOpen,
    panel,
    storagePosKey: STORAGE_POS_KEY,
    widget,
  });
  const connectionController = connectionApi.createConnectionController({
    WebSocketCtor: window.WebSocket,
    renderer,
    setStatus,
    userId,
    wsUrl,
  });

  function setStatus(label, state) {
    if (!statusEl) return;
    statusEl.textContent = label;
    statusEl.classList.remove("is-connected", "is-connecting", "is-disconnected");
    if (state) statusEl.classList.add(`is-${state}`);
  }

  function setUnreadDot(on) {
    hasUnread = !!on;
    if (!badge) return;

    badge.hidden = !hasUnread;

    try {
      localStorage.setItem(STORAGE_UNREAD_KEY, hasUnread ? "1" : "0");
    } catch (e) {
      // ignore storage failures
    }
  }

  function loadUnreadDot() {
    try {
      const raw = localStorage.getItem(STORAGE_UNREAD_KEY);
      if (raw === "1") {
        hasUnread = true;
        badge.hidden = false;
      }
    } catch (e) {
      // ignore
    }
  }

  function setOpen(open) {
    isOpen = !!open;
    panel.classList.toggle("is-open", isOpen);
    panel.setAttribute("aria-hidden", isOpen ? "false" : "true");
    fab.setAttribute("aria-expanded", isOpen ? "true" : "false");

    try {
      localStorage.setItem(STORAGE_OPEN_KEY, isOpen ? "1" : "0");
    } catch (e) {
      // ignore storage failures
    }

    if (isOpen) {
      setUnreadDot(false);
      requestAnimationFrame(() => {
        layoutController.layoutPanel();
        messagesEl.scrollTop = messagesEl.scrollHeight;
        inputEl.focus();
      });
    }
  }

  function sendCurrent() {
    const text = core.normalizeOutgoingText(inputEl.value || "");
    if (!text) return;

    if (!connectionController.sendText(text)) {
      return;
    }

    inputEl.value = "";
    sendBtn.disabled = true;
    inputEl.focus();
  }

  function updateSendState() {
    sendBtn.disabled = !(inputEl.value || "").trim();
  }

  fab.addEventListener("pointerdown", (e) => {
    if (isOpen) return;
    layoutController.handlePointerDown(e, fab);
  });

  headerEl.addEventListener("pointerdown", (e) => {
    if (e.target && e.target.closest && e.target.closest("button")) return;
    layoutController.handlePointerDown(e, headerEl);
  });

  fab.addEventListener("click", () => {
    if (layoutController.shouldSuppressClick(Date.now())) return;
    setOpen(!isOpen);
  });
  closeBtn.addEventListener("click", () => setOpen(false));
  clearBtn.addEventListener("click", () => {
    renderer.clearMessages();
    renderer.appendSystem("已清屏");
  });
  sendBtn.addEventListener("click", sendCurrent);

  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendCurrent();
    }
  });
  inputEl.addEventListener("input", updateSendState);

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && isOpen) {
      setOpen(false);
    }
  });

  document.addEventListener("mousedown", (e) => {
    if (!isOpen) return;
    if (e.target && widget.contains(e.target)) return;
    setOpen(false);
  });
  layoutController.loadWidgetPos();
  loadUnreadDot();

  try {
    const saved = localStorage.getItem(STORAGE_OPEN_KEY);
    if (saved === "1") {
      setOpen(true);
    } else {
      setOpen(false);
    }
  } catch (e) {
    setOpen(false);
  }

  updateSendState();
  connectionController.connect();

  pruneTimer = setInterval(renderer.pruneOldMessages, 30000);

  window.addEventListener("beforeunload", () => {
    if (pruneTimer) clearInterval(pruneTimer);
    layoutController.teardown();
    connectionController.teardown();
  });
})();
