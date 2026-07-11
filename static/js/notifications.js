(function () {
  const getSidebarLink = () => document.getElementById("nav-messages-link");
  const getTopLink = () => document.getElementById("nav-messages-link-top");

  function readCurrentUnreadCount() {
    const link = getSidebarLink() || getTopLink();
    if (!link) return null;
    const unread = link.dataset.unread ?? "0";
    const count = parseInt(unread, 10);
    return Number.isNaN(count) ? 0 : count;
  }

  const toastContainerId = "toast-container";
  const wsPath = "/ws/notifications/";
  const INITIAL_RECONNECT_DELAY = 2000;
  const MAX_RECONNECT_DELAY = 15000;
  const STABLE_CONNECTION_DELAY = 30000;
  const RECONNECT_JITTER = 0.1;
  const TERMINAL_CLOSE_CODES = new Set([4401, 4403]);
  const TOP_LEVEL_NOTIFICATION_FIELDS = new Set(["type", "kind", "title", "body", "timestamp", "message"]);
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  const wsUrl = `${scheme}://${window.location.host}${wsPath}`;

  let socket;
  let reconnectDelay = INITIAL_RECONNECT_DELAY;
  let reconnectTimer = null;
  let stabilityTimer = null;
  let currentUnreadCount = readCurrentUnreadCount();
  let refreshBannerShown = false; // 防止重复显示刷新提示

  // Performance optimization: show a non-intrusive refresh banner instead of auto-reload
  function showRefreshBanner(message) {
    if (refreshBannerShown) return;
    refreshBannerShown = true;

    const banner = document.createElement("div");
    banner.id = "refresh-banner";
    banner.style.cssText = `
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      background: linear-gradient(135deg, var(--accent-gold, #DAA520), var(--accent-red, #DC143C));
      color: white;
      padding: 10px 20px;
      text-align: center;
      z-index: 10000;
      font-size: 14px;
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 15px;
      box-shadow: 0 2px 10px rgba(0,0,0,0.3);
    `;

    const textSpan = document.createElement("span");
    textSpan.textContent = message || "页面内容已更新";

    const refreshBtn = document.createElement("button");
    refreshBtn.textContent = "点击刷新";
    refreshBtn.style.cssText = `
      background: white;
      color: var(--accent-red, #DC143C);
      border: none;
      padding: 5px 15px;
      border-radius: 4px;
      cursor: pointer;
      font-weight: bold;
    `;
    refreshBtn.onclick = () => window.location.reload();

    const dismissBtn = document.createElement("button");
    dismissBtn.textContent = "稍后";
    dismissBtn.style.cssText = `
      background: transparent;
      color: white;
      border: 1px solid white;
      padding: 5px 15px;
      border-radius: 4px;
      cursor: pointer;
    `;
    dismissBtn.onclick = () => {
      banner.remove();
      // Allow showing banner again after 30 seconds
      setTimeout(() => { refreshBannerShown = false; }, 30000);
    };

    banner.appendChild(textSpan);
    banner.appendChild(refreshBtn);
    banner.appendChild(dismissBtn);
    document.body.appendChild(banner);
  }

  // Keep legacy function for backward compatibility, but use banner instead
  function scheduleReload(message) {
    showRefreshBanner(message);
  }

  function renderUnreadCount() {
    const sidebarLink = getSidebarLink();
    const topLink = getTopLink();
    if (!sidebarLink && !topLink) return;
    if (currentUnreadCount === null) {
      currentUnreadCount = readCurrentUnreadCount() ?? 0;
    }

    // 更新侧边栏消息链接文本
    if (sidebarLink) {
      if (currentUnreadCount > 0) {
        sidebarLink.textContent = `消息 (${currentUnreadCount})`;
      } else {
        sidebarLink.textContent = '消息';
      }
    }

    // 更新顶部导航角标
    if (topLink) {
      // 查找现有的角标元素
      let badge = topLink.querySelector(".nav-badge");

      if (currentUnreadCount > 0) {
        if (!badge) {
          // 创建新的角标
          badge = document.createElement("span");
          badge.className = "nav-badge";
          topLink.appendChild(badge);
        }
        badge.textContent = String(currentUnreadCount);
        badge.style.display = "";
      } else if (badge) {
        // 隐藏角标
        badge.style.display = "none";
      }
    }

    // 更新 data-unread 属性
    if (sidebarLink) sidebarLink.dataset.unread = String(currentUnreadCount);
    if (topLink) topLink.dataset.unread = String(currentUnreadCount);
  }

  function updateUnreadCount(increment = 1) {
    if (currentUnreadCount === null) {
      currentUnreadCount = readCurrentUnreadCount();
      if (currentUnreadCount === null) return;
    }
    currentUnreadCount += increment;
    renderUnreadCount();
  }

  function dismissToast(toast) {
    toast.classList.add("toast-leaving");
    setTimeout(() => toast.remove(), 300);
  }

  function getToastIcon(kind) {
    const icons = {
      battle: "战",
      trade: "市",
      guild: "帮",
      reward: "赏",
    };
    return icons[kind] || null;
  }

  function showToast({ title, body, kind }) {
    const container = document.getElementById(toastContainerId);
    if (!container) return;
    const toastKind = kind || "system";
    const toast = document.createElement("div");
    toast.className = `toast toast-${toastKind}`;
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-live", "polite");

    const toastIcon = getToastIcon(toastKind);

    const contentEl = document.createElement("div");
    contentEl.className = "toast-content";

    const titleEl = document.createElement("div");
    titleEl.className = "toast-title";
    titleEl.textContent = title || "新通知";

    const bodyEl = document.createElement("div");
    bodyEl.className = "toast-body";
    bodyEl.textContent = body || "";

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "toast-close";
    closeBtn.setAttribute("aria-label", "关闭通知");
    closeBtn.textContent = "×";
    closeBtn.addEventListener("click", () => dismissToast(toast));

    contentEl.appendChild(titleEl);
    if (bodyEl.textContent) {
      contentEl.appendChild(bodyEl);
    }
    if (toastIcon) {
      const iconEl = document.createElement("div");
      iconEl.className = "toast-icon";
      iconEl.setAttribute("aria-hidden", "true");
      iconEl.textContent = toastIcon;
      toast.appendChild(iconEl);
    }
    toast.appendChild(contentEl);
    toast.appendChild(closeBtn);
    container.appendChild(toast);
    setTimeout(() => dismissToast(toast), 5000);
  }

  function handlePayload(payload) {
    if (!payload) return;
    const detail = (typeof payload.data === "object" && payload.data) || {};
    const field = (name) => (
      TOP_LEVEL_NOTIFICATION_FIELDS.has(name) ? payload[name] : detail[name] ?? payload[name]
    );
    const kind = field("kind") || "";

    if (kind === "system" && field("building_key")) {
      updateUnreadCount(1);
      showToast({
        title: field("title") || "建筑升级完成",
        body: `当前等级 Lv${field("level") ?? "?"}`,
        kind: "system",
      });
      // 如果在庄园页面，自动刷新
      if (window.location.pathname.includes("/gameplay/") || window.location.pathname === "/") {
        scheduleReload();
      }
      return;
    }
    if (kind === "system" && field("tech_key")) {
      updateUnreadCount(1);
      showToast({
        title: field("title") || "技术研究完成",
        body: `当前等级 Lv${field("level") ?? "?"}`,
        kind: "system",
      });
      // 如果在技术页面，自动刷新
      if (window.location.pathname.includes("/technology")) {
        scheduleReload();
      }
      return;
    }
    if (kind === "battle" || kind === "mission") {
      updateUnreadCount(1);
      const missionLabel = field("mission_name") || field("title") || field("mission_key") || "";
      showToast({
        title: field("title") || "战报更新",
        body: missionLabel ? `${missionLabel} 已完成` : "战斗结果已生成",
        kind: "battle",
      });
      // 如果在相关页面，自动刷新以显示更新的任务列表和门客状态
      const path = window.location.pathname;
      if (path.includes("/gameplay/tasks") ||
          (path.includes("/gameplay/") && path.endsWith("/")) ||
          path.includes("/guests/roster")) {
        scheduleReload();
      }
      return;
    }
    if (kind === "auction_won" || kind === "auction_outbid") {
      updateUnreadCount(1);
      const itemName = field("item_name") || "拍卖物品";
      let auctionBody = field("body") || itemName;
      if (!field("body") && kind === "auction_won") {
        const quantity = field("quantity");
        const price = field("price");
        const quantityText = quantity ? ` x${quantity}` : "";
        const priceText = price != null ? `，成交价 ${price} 金条` : "";
        auctionBody = `${itemName}${quantityText}${priceText}`;
      } else if (!field("body")) {
        const price = field("new_price");
        auctionBody = `${itemName}${price != null ? `，当前最低中标价 ${price} 金条` : ""}`;
      }
      showToast({
        title: field("title") || "拍卖动态",
        body: auctionBody,
        kind: "trade",
      });
      if (window.location.pathname.includes("/auction")) {
        scheduleReload();
      }
      return;
    }
    updateUnreadCount(1);
    showToast({
      title: field("title") || "新消息",
      body: field("body") || field("message") || "",
      kind,
    });
  }

  function clearStabilityTimer() {
    if (stabilityTimer === null) return;
    clearTimeout(stabilityTimer);
    stabilityTimer = null;
  }

  function resetReconnectDelay() {
    reconnectDelay = INITIAL_RECONNECT_DELAY;
    clearStabilityTimer();
  }

  function reconnectDelayWithJitter() {
    const jitterFactor = 1 - RECONNECT_JITTER + (Math.random() * RECONNECT_JITTER * 2);
    return Math.min(MAX_RECONNECT_DELAY, Math.round(reconnectDelay * jitterFactor));
  }

  function connect() {
    const currentSocket = new WebSocket(wsUrl);
    socket = currentSocket;

    currentSocket.onopen = () => {
      if (socket !== currentSocket) return;
      clearStabilityTimer();
      const scheduledStabilityTimer = setTimeout(() => {
        if (socket !== currentSocket || stabilityTimer !== scheduledStabilityTimer) return;
        stabilityTimer = null;
        reconnectDelay = INITIAL_RECONNECT_DELAY;
      }, STABLE_CONNECTION_DELAY);
      stabilityTimer = scheduledStabilityTimer;
    };

    currentSocket.onmessage = (event) => {
      if (socket !== currentSocket) return;
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (e) {
        return;
      }
      resetReconnectDelay();
      handlePayload(payload);
    };

    currentSocket.onclose = (event) => {
      if (socket !== currentSocket) return;
      clearStabilityTimer();
      if (event && TERMINAL_CLOSE_CODES.has(event.code)) {
        if (reconnectTimer !== null) {
          clearTimeout(reconnectTimer);
          reconnectTimer = null;
        }
        return;
      }
      if (reconnectTimer !== null) return;
      const scheduledReconnectTimer = setTimeout(() => {
        if (socket !== currentSocket || reconnectTimer !== scheduledReconnectTimer) return;
        reconnectTimer = null;
        connect();
      }, reconnectDelayWithJitter());
      reconnectTimer = scheduledReconnectTimer;
      reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY);
    };

    currentSocket.onerror = () => {
      if (socket !== currentSocket) return;
      currentSocket.close();
    };
  }

  document.addEventListener("DOMContentLoaded", () => {
    // 连接 WebSocket
    connect();
  });
  document.addEventListener("partial-nav:loaded", renderUnreadCount);
})();
