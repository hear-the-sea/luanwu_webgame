(function (globalScope) {
  "use strict";

  const bannerId = "global-announcement-banner";
  let expiryTimer = null;

  function parseExpiry(value) {
    const timestamp = Date.parse(value || "");
    return Number.isFinite(timestamp) ? timestamp : null;
  }

  function removeBanner() {
    if (expiryTimer !== null) {
      globalScope.clearTimeout(expiryTimer);
      expiryTimer = null;
    }
    const banner = globalScope.document?.getElementById(bannerId);
    if (banner) banner.remove();
  }

  function scheduleExpiry(banner) {
    if (expiryTimer !== null) globalScope.clearTimeout(expiryTimer);
    const expiresAt = parseExpiry(banner.dataset.expiresAt);
    if (expiresAt === null) return;
    const remaining = expiresAt - Date.now();
    if (remaining <= 0) {
      removeBanner();
      return;
    }
    expiryTimer = globalScope.setTimeout(removeBanner, remaining);
  }

  function getFlashContainer() {
    const document = globalScope.document;
    if (!document) return null;
    let container = document.querySelector("main.content-area .flash-messages");
    if (container) return container;

    const main = document.querySelector("main.content-area") || document.querySelector("main");
    if (!main) return null;

    container = document.createElement("ul");
    container.className = "flash-messages";
    main.insertBefore(container, main.firstChild);
    return container;
  }

  function ensureBanner() {
    const document = globalScope.document;
    if (!document) return null;
    let banner = document.getElementById(bannerId);
    if (banner) return banner;

    const container = getFlashContainer();
    if (!container) return null;

    banner = document.createElement("li");
    banner.id = bannerId;
    banner.className = "flash success global-announcement-banner";
    banner.setAttribute("role", "status");
    banner.setAttribute("aria-live", "polite");
    const title = document.createElement("strong");
    title.className = "global-announcement-title";
    const body = document.createElement("span");
    body.className = "global-announcement-body";
    banner.appendChild(title);
    banner.appendChild(body);
    container.insertBefore(banner, container.firstChild);
    return banner;
  }

  function render(payload) {
    if (!payload || !globalScope.document?.body) return;
    const expiresAt = payload.expires_at || payload.expiresAt;
    if (parseExpiry(expiresAt) === null || parseExpiry(expiresAt) <= Date.now()) {
      removeBanner();
      return;
    }

    const banner = ensureBanner();
    if (!banner) return;
    if (payload.announcement_id || payload.id) {
      banner.dataset.announcementId = String(payload.announcement_id || payload.id);
    }
    banner.dataset.expiresAt = String(expiresAt);
    if (payload.title !== undefined) {
      banner.querySelector(".global-announcement-title").textContent = String(payload.title || "全服公告");
    }
    if (payload.body !== undefined) {
      banner.querySelector(".global-announcement-body").textContent = String(payload.body || "");
    }
    scheduleExpiry(banner);
  }

  function init() {
    const banner = globalScope.document?.getElementById(bannerId);
    if (banner) scheduleExpiry(banner);
  }

  globalScope.GlobalAnnouncement = { init, render };
  if (globalScope.document) {
    if (globalScope.document.readyState === "loading") {
      globalScope.document.addEventListener("DOMContentLoaded", init);
    } else {
      init();
    }
    globalScope.document.addEventListener("partial-nav:loaded", init);
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
