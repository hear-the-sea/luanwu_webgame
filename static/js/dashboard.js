(function () {
  const pad = (num) => String(num).padStart(2, "0");

  // Performance optimization: cache countdown elements and parsed timestamps
  const countdownCache = new Map(); // el -> { target, style, shouldRefresh, checkUrl, completeText, removeSelector }
  let missionLists = []; // cached mission list elements
  let cacheInitialized = false;
  let observerInitialized = false;
  let mutationObserver = null;
  let observedMutationRoot = null;
  let tickTimerId = null;
  let reloadTimerId = null;
  let refreshRequestInFlight = false;
  const AUTO_REFRESH_GRACE_MS = 800;
  const SHORT_COUNTDOWN_WINDOW_MS = 5000;
  const SHORT_COUNTDOWN_GRACE_MS = 1200;
  const TRAINING_CHECK_TIMEOUT_MS = 10000;
  const TRAINING_CHECK_RETRY_BASE_MS = 2000;
  const TRAINING_CHECK_RETRY_MAX_MS = 30000;
  const trainingCheckInFlight = new WeakSet();
  const trainingCheckRetryAttempts = new WeakMap();
  const trainingCheckRetryTimers = new WeakMap();

  function schedulePageReload() {
    if (reloadTimerId !== null) return;
    reloadTimerId = window.setTimeout(() => window.location.reload(), 500);
  }

  function requestRefreshThenReload(refreshUrl, refreshMethod) {
    if (!refreshUrl) {
      schedulePageReload();
      return;
    }
    if (refreshRequestInFlight) return;
    refreshRequestInFlight = true;

    const method = (refreshMethod || "GET").toUpperCase();
    const headers = { "X-Requested-With": "XMLHttpRequest" };
    if (method !== "GET" && method !== "HEAD") {
      const csrfToken = getCSRFToken();
      if (csrfToken) {
        headers["X-CSRFToken"] = csrfToken;
      }
    }

    fetch(refreshUrl, {
      method,
      headers,
    })
      .catch((err) => {
        console.error("状态刷新失败:", err);
      })
      .finally(() => {
        schedulePageReload();
      });
  }

  function refreshCache() {
    const root = getMutationRoot();
    const querySelectorAll = root && typeof root.querySelectorAll === "function"
      ? root.querySelectorAll.bind(root)
      : document.querySelectorAll.bind(document);
    countdownCache.clear();
    querySelectorAll("[data-countdown]").forEach(registerCountdownElement);
    missionLists = Array.from(querySelectorAll(".mission-list"));
    cacheInitialized = true;
  }

  function initCache() {
    if (cacheInitialized) return;
    refreshCache();
  }

  function registerCountdownElement(el) {
    const targetIso = el.getAttribute("data-countdown");
    if (!targetIso) return;
    const target = Date.parse(targetIso);
    if (Number.isNaN(target)) return;
    const initialRemainingMs = target - Date.now();
    countdownCache.set(el, {
      target,
      style: el.getAttribute("data-format") || "",
      shouldRefresh: el.getAttribute("data-refresh") === "1",
      refreshUrl: el.getAttribute("data-refresh-url"),
      refreshMethod: el.getAttribute("data-refresh-method"),
      checkUrl: el.getAttribute("data-check-url"),
      completeText: el.getAttribute("data-complete-text"),
      removeSelector: el.getAttribute("data-remove-selector"),
      initialRemainingMs,
      lastText: null,
    });
  }

  function unregisterCountdownElement(el) {
    countdownCache.delete(el);
  }

  function isElementConnected(el) {
    if (typeof el.isConnected === "boolean") {
      return el.isConnected;
    }
    return document.body.contains(el);
  }

  function setCountdownText(el, cached, text) {
    if (cached.lastText === text && el.textContent === text) {
      return;
    }
    el.textContent = text;
    cached.lastText = text;
  }

  function getMutationRoot() {
    return document.querySelector("#page-shell") || document.body;
  }

  function observeMutationRoot() {
    if (!mutationObserver) return;
    const nextRoot = getMutationRoot();
    if (!nextRoot || nextRoot === observedMutationRoot) return;
    if (observedMutationRoot && typeof mutationObserver.disconnect === "function") {
      mutationObserver.disconnect();
    }
    mutationObserver.observe(nextRoot, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["data-countdown"]
    });
    observedMutationRoot = nextRoot;
  }

  // Observe DOM changes to update cache automatically
  function setupMutationObserver() {
    if (observerInitialized) return;
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        // Handle added nodes
        for (const node of mutation.addedNodes) {
          if (node.nodeType !== Node.ELEMENT_NODE) continue;
          if (node.hasAttribute && node.hasAttribute("data-countdown")) {
            registerCountdownElement(node);
          }
          // Check descendants
          if (node.querySelectorAll) {
            node.querySelectorAll("[data-countdown]").forEach(registerCountdownElement);
            node.querySelectorAll(".mission-list").forEach((list) => {
              if (!missionLists.includes(list)) missionLists.push(list);
            });
          }
        }
        // Handle removed nodes
        for (const node of mutation.removedNodes) {
          if (node.nodeType !== Node.ELEMENT_NODE) continue;
          if (countdownCache.has(node)) {
            unregisterCountdownElement(node);
          }
          if (node.querySelectorAll) {
            node.querySelectorAll("[data-countdown]").forEach(unregisterCountdownElement);
            const removedLists = node.querySelectorAll(".mission-list");
            if (removedLists.length > 0) {
              const removedSet = new Set(Array.from(removedLists));
              missionLists = missionLists.filter((list) => !removedSet.has(list));
            }
            if (node.classList && node.classList.contains("mission-list")) {
              missionLists = missionLists.filter((list) => list !== node);
            }
          }
        }
        // Handle attribute changes on data-countdown
        if (mutation.type === "attributes" && mutation.attributeName === "data-countdown") {
          const el = mutation.target;
          if (el.hasAttribute("data-countdown")) {
            registerCountdownElement(el);
          } else {
            unregisterCountdownElement(el);
          }
        }
      }
    });
    mutationObserver = observer;
    observerInitialized = true;
    observeMutationRoot();
  }

  function refreshAfterPartialNavigation() {
    if (!observerInitialized) return;
    refreshCache();
    observeMutationRoot();
    tick();
  }

  function formatCountdown(diffMs, style) {
    if (diffMs <= 0) return style === "zh" || style === "zh-no-sec" ? "已返程" : "完成";
    const totalSeconds = Math.floor(diffMs / 1000);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    if (style === "zh-no-sec") {
      // 不显示秒，向上取整分钟
      const roundedMinutes = seconds > 0 ? minutes + 1 : minutes;
      const parts = [];
      if (hours > 0) parts.push(`${hours}小时`);
      parts.push(`${roundedMinutes}分钟`);
      return parts.join("");
    }
    if (style === "zh") {
      const parts = [];
      if (hours > 0) parts.push(`${hours}小时`);
      if (hours > 0 || minutes > 0) parts.push(`${minutes}分钟`);
      parts.push(`${seconds}秒`);
      return parts.join("");
    }
    if (hours > 0) {
      return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
    }
    return `${pad(minutes)}:${pad(seconds)}`;
  }

  function ensureListPlaceholder(listEl) {
    if (!listEl) return;
    const hasActive = listEl.querySelector(".mission-item:not(.no-mission-placeholder)");
    let placeholder = listEl.querySelector(".no-mission-placeholder");
    if (hasActive) {
      if (placeholder) placeholder.remove();
      return;
    }
    if (!placeholder) {
      placeholder = document.createElement("li");
      placeholder.className = "mission-item no-mission-placeholder muted-text";
      placeholder.textContent = "暂无出征中的门客。";
      listEl.appendChild(placeholder);
    }
  }

  // 获取 CSRF token
  function getCSRFToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    const metaToken = meta ? meta.getAttribute("content") : "";
    if (metaToken && metaToken !== "NOTPROVIDED") {
      return metaToken;
    }

    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (input && input.value) {
      return input.value;
    }

    const cookie = document.cookie
      .split("; ")
      .find((row) => row.startsWith("csrftoken="));
    return cookie ? decodeURIComponent(cookie.split("=")[1]) : "";
  }

  function clearTrainingCheckRetry(el) {
    const retryTimerId = trainingCheckRetryTimers.get(el);
    if (retryTimerId !== undefined) {
      window.clearTimeout(retryTimerId);
      trainingCheckRetryTimers.delete(el);
    }
    trainingCheckRetryAttempts.delete(el);
  }

  function scheduleTrainingCheckRetry(el) {
    if (!isElementConnected(el)) return;

    const attempt = (trainingCheckRetryAttempts.get(el) || 0) + 1;
    const retryDelay = Math.min(
      TRAINING_CHECK_RETRY_BASE_MS * (2 ** (attempt - 1)),
      TRAINING_CHECK_RETRY_MAX_MS,
    );
    trainingCheckRetryAttempts.set(el, attempt);
    el.textContent = "检查失败，重试中...";
    el.classList.remove("countdown-finished");

    const previousTimerId = trainingCheckRetryTimers.get(el);
    if (previousTimerId !== undefined) {
      window.clearTimeout(previousTimerId);
    }
    const retryTimerId = window.setTimeout(() => {
      trainingCheckRetryTimers.delete(el);
      if (isElementConnected(el)) {
        checkTrainingAndUpdate(el);
      }
    }, retryDelay);
    trainingCheckRetryTimers.set(el, retryTimerId);
  }

  // 调用检查训练完成的 API 并更新行数据
  async function checkTrainingAndUpdate(el) {
    const checkUrl = el.getAttribute("data-check-url");
    if (!checkUrl) return false;

    const row = el.closest("tr[data-guest-id]");
    if (!row || trainingCheckInFlight.has(el)) return false;

    trainingCheckInFlight.add(el);
    const abortController = typeof window.AbortController === "function" ? new window.AbortController() : null;
    const requestTimerId = abortController
      ? window.setTimeout(() => abortController.abort(), TRAINING_CHECK_TIMEOUT_MS)
      : null;

    try {
      el.textContent = "检查中...";
      const resp = await fetch(checkUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          "X-CSRFToken": getCSRFToken(),
        },
        signal: abortController ? abortController.signal : undefined,
      });
      const data = await resp.json();
      if (resp.ok === false || !data || data.success !== true) {
        throw new Error((data && (data.error || data.message)) || `训练状态检查失败 (${resp.status})`);
      }

      clearTrainingCheckRetry(el);
      // 更新等级
      const levelDiv = row.querySelector(".guest-level");
      if (levelDiv) {
        levelDiv.textContent = `等级 ${data.level}`;
      }
      // 更新生命值 - 使用类名选择器代替硬编码索引
      const hpDiv = row.querySelector(".guest-hp");
      if (hpDiv) {
        hpDiv.textContent = `生命 ${data.current_hp}/${data.max_hp}`;
      }
      // 更新训练倒计时 - 使用 el 的父元素
      const upgradeCell = el.closest("td");
      if (data.training_paused) {
        if (upgradeCell) {
          upgradeCell.textContent = '';
          const span = document.createElement('span');
          span.className = 'muted-text';
          span.textContent = '已暂停';
          upgradeCell.appendChild(span);
        }
      } else if (data.training_eta) {
        el.setAttribute("data-countdown", data.training_eta);
        el.classList.remove("countdown-finished");
        el.textContent = "计算中";
      } else {
        // 已完成，显示"自动升级"
        // 安全修复：使用 DOM API 替代 innerHTML，防止 XSS
        if (upgradeCell) {
          upgradeCell.textContent = '';
          const span = document.createElement('span');
          span.className = 'muted-text';
          span.textContent = '自动升级';
          upgradeCell.appendChild(span);
        }
      }
      return true;
    } catch (err) {
      console.error("检查训练状态失败:", err);
      scheduleTrainingCheckRetry(el);
    } finally {
      if (requestTimerId !== null) {
        window.clearTimeout(requestTimerId);
      }
      trainingCheckInFlight.delete(el);
    }
    return false;
  }

  function tick() {
    const now = Date.now();
    const ensureEmptyPlaceholder = (removedEl) => {
      if (!removedEl) return;
      const list = removedEl.closest(".mission-list");
      ensureListPlaceholder(list);
    };

    // Use cached elements instead of querying DOM every second
    const toRemove = [];
    for (const [el, cached] of countdownCache) {
      // Check if element is still in DOM
      if (!isElementConnected(el)) {
        toRemove.push(el);
        continue;
      }

      const {
        target,
        style,
        shouldRefresh,
        refreshUrl,
        refreshMethod,
        checkUrl,
        completeText,
        removeSelector,
        initialRemainingMs,
      } = cached;
      const diff = target - now;
      const completionGraceMs =
        shouldRefresh && initialRemainingMs <= SHORT_COUNTDOWN_WINDOW_MS
          ? SHORT_COUNTDOWN_GRACE_MS
          : AUTO_REFRESH_GRACE_MS;
      const readyToComplete = shouldRefresh ? diff <= -completionGraceMs : diff <= 0;

      if (readyToComplete) {
        if (shouldRefresh || checkUrl) {
          setCountdownText(el, cached, "刷新中...");
        } else if (completeText) {
          setCountdownText(el, cached, completeText);
        } else {
          setCountdownText(el, cached, formatCountdown(diff, style));
        }
        el.classList.add("countdown-finished");
        el.removeAttribute("data-countdown");
        toRemove.push(el);

        if (removeSelector) {
          const targetEl = el.closest(removeSelector);
          if (targetEl) {
            targetEl.remove();
            ensureEmptyPlaceholder(targetEl);
          }
        }
        // 优先使用 AJAX 检查（如果配置了 data-check-url）
        if (checkUrl) {
          checkTrainingAndUpdate(el);
        } else if (shouldRefresh) {
          requestRefreshThenReload(refreshUrl, refreshMethod);
        }
      } else if (shouldRefresh && diff <= 0) {
        // 倒计时到点后留出短暂缓冲，减少短计时建筑因节流/时钟偏差导致的重复刷新。
        setCountdownText(el, cached, "结算中...");
      } else {
        setCountdownText(el, cached, formatCountdown(diff, style));
      }
    }

    // Clean up finished countdowns from cache
    toRemove.forEach((el) => countdownCache.delete(el));

    // Use cached mission lists instead of querying DOM
    missionLists = missionLists.filter((listEl) => {
      if (isElementConnected(listEl)) {
        ensureListPlaceholder(listEl);
        return true;
      }
      return false;
    });
  }

  function boot() {
    if (tickTimerId !== null) return;
    initCache();
    setupMutationObserver();
    tick();
    tickTimerId = setInterval(tick, 1000);
  }

  if (typeof document.addEventListener === "function") {
    document.addEventListener("partial-nav:loaded", refreshAfterPartialNavigation);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
