(function () {
  "use strict";

  if (
    !window.fetch
    || !window.DOMParser
    || !window.AbortController
    || !window.history
    || !window.history.pushState
  ) {
    return;
  }

  const NAV_LINK_SELECTOR = '.game-nav a.nav-tab[data-partial-nav="1"]';
  const EXTRA_SCRIPTS_ID = "page-extra-scripts";
  const PAGE_SHELL_ID = "page-shell";
  const AUTHENTICATED_SHELL_ATTRIBUTE = "data-authenticated";
  const SECTION_IDS = ["main-nav", "info-bar", PAGE_SHELL_ID];
  const EXTRA_HEAD_START_MARKER = "PAGE_EXTRA_HEAD_START";
  const EXTRA_HEAD_END_MARKER = "PAGE_EXTRA_HEAD_END";
  const partialNavCore = window.PartialNavCore;
  const allowedPaths = new Set(
    Array.from(document.querySelectorAll(NAV_LINK_SELECTOR))
      .map((link) => {
        try {
          return new URL(link.href, window.location.href).pathname;
        } catch (error) {
          return "";
        }
      })
      .filter(Boolean)
  );

  let requestSeq = 0;
  let activeNavigationController = null;
  const loadedScriptUrls = new Set(
    Array.from(document.querySelectorAll("script[src]"))
      .map((scriptEl) => {
        try {
          return new URL(scriptEl.getAttribute("src"), window.location.href).href;
        } catch (error) {
          return "";
        }
      })
      .filter(Boolean)
  );

  function isSameOrigin(url) {
    try {
      return new URL(url, window.location.href).origin === window.location.origin;
    } catch (error) {
      return false;
    }
  }

  function shouldHandleClick(event, link) {
    if (!link || event.defaultPrevented) return false;
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
    if (link.target && link.target.toLowerCase() !== "_self") return false;
    if (link.hasAttribute("download")) return false;

    const rawHref = link.getAttribute("href");
    if (!rawHref || rawHref.startsWith("#")) return false;
    if (!isSameOrigin(link.href)) return false;

    const currentUrl = new URL(window.location.href);
    const targetUrl = new URL(link.href, window.location.href);
    if (targetUrl.pathname === currentUrl.pathname && targetUrl.search === currentUrl.search) {
      return false;
    }
    return true;
  }

  function isAllowedPartialUrl(url) {
    try {
      const parsed = new URL(url, window.location.href);
      return allowedPaths.has(parsed.pathname);
    } catch (error) {
      return false;
    }
  }

  async function fetchDocument(url, signal) {
    const response = await fetch(url, {
      method: "GET",
      credentials: "same-origin",
      headers: {
        "X-Requested-With": "XMLHttpRequest",
        "X-Partial-Navigation": "1",
      },
      signal,
    });
    if (!response.ok) {
      throw new Error("failed to fetch page");
    }
    const contentType = response.headers.get("content-type") || "";
    if (contentType && !contentType.includes("text/html")) {
      throw new Error("response is not html");
    }

    const html = await response.text();
    return {
      document: new DOMParser().parseFromString(html, "text/html"),
      redirected: response.redirected === true,
      finalUrl: typeof response.url === "string" ? response.url.trim() : "",
    };
  }

  function replaceSectionById(id, nextDocument) {
    const currentEl = document.getElementById(id);
    const nextEl = nextDocument.getElementById(id);
    if (!currentEl || !nextEl) {
      return false;
    }
    currentEl.replaceWith(nextEl);
    return true;
  }

  function replaceCoreSections(nextDocument) {
    for (const id of SECTION_IDS) {
      if (!document.getElementById(id) || !nextDocument.getElementById(id)) {
        return false;
      }
    }

    SECTION_IDS.forEach((id) => {
      replaceSectionById(id, nextDocument);
    });
    return true;
  }

  function isAuthenticatedDocument(nextDocument) {
    const nextShell = nextDocument.getElementById(PAGE_SHELL_ID);
    return Boolean(
      nextShell
      && typeof nextShell.getAttribute === "function"
      && nextShell.getAttribute(AUTHENTICATED_SHELL_ATTRIBUTE) === "1"
    );
  }

  function sameRequestLocation(leftUrl, rightUrl) {
    return leftUrl.origin === rightUrl.origin
      && leftUrl.pathname === rightUrl.pathname
      && leftUrl.search === rightUrl.search;
  }

  function responseHardNavigationUrl(targetUrl, fetchResult) {
    if (!fetchResult.finalUrl) {
      return fetchResult.redirected ? targetUrl.href : "";
    }

    let finalUrl;
    try {
      finalUrl = new URL(fetchResult.finalUrl, targetUrl.href);
    } catch (error) {
      return targetUrl.href;
    }
    if (fetchResult.redirected || !sameRequestLocation(finalUrl, targetUrl)) {
      return finalUrl.href;
    }
    return "";
  }

  function findHeadMarkers(doc) {
    if (!doc || !doc.head) {
      return { start: null, end: null };
    }
    let start = null;
    let end = null;
    Array.from(doc.head.childNodes).forEach((node) => {
      if (node.nodeType !== Node.COMMENT_NODE) return;
      const marker = (node.nodeValue || "").trim();
      if (marker === EXTRA_HEAD_START_MARKER) start = node;
      if (marker === EXTRA_HEAD_END_MARKER) end = node;
    });
    return { start, end };
  }

  function syncExtraHead(nextDocument) {
    const currentMarkers = findHeadMarkers(document);
    const nextMarkers = findHeadMarkers(nextDocument);
    if (!currentMarkers.start || !currentMarkers.end || !nextMarkers.start || !nextMarkers.end) {
      return;
    }

    // Remove previous per-page head nodes.
    let currentNode = currentMarkers.start.nextSibling;
    while (currentNode && currentNode !== currentMarkers.end) {
      const nextSibling = currentNode.nextSibling;
      currentNode.remove();
      currentNode = nextSibling;
    }

    // Insert new page-specific head nodes. Skip scripts to avoid global re-execution issues.
    let nextNode = nextMarkers.start.nextSibling;
    while (nextNode && nextNode !== nextMarkers.end) {
      if (!(nextNode.nodeType === Node.ELEMENT_NODE && nextNode.tagName === "SCRIPT")) {
        document.head.insertBefore(nextNode.cloneNode(true), currentMarkers.end);
      }
      nextNode = nextNode.nextSibling;
    }
  }

  function executeInlineScript(code) {
    const originalAddEventListener = document.addEventListener;
    document.addEventListener = function patchedAddEventListener(type, listener, options) {
      if (type === "DOMContentLoaded") {
        try {
          if (typeof listener === "function") {
            listener.call(document, new Event("DOMContentLoaded"));
          } else if (listener && typeof listener.handleEvent === "function") {
            listener.handleEvent(new Event("DOMContentLoaded"));
          }
        } catch (error) {
          console.error("[partial-nav] dom content loaded callback failed", error);
        }
        return;
      }
      return originalAddEventListener.call(document, type, listener, options);
    };

    try {
      const script = document.createElement("script");
      script.textContent = code;
      document.body.appendChild(script);
      script.remove();
    } finally {
      document.addEventListener = originalAddEventListener;
    }
  }

  async function executePageScripts(nextDocument, signal) {
    const scriptContainer = nextDocument.getElementById(EXTRA_SCRIPTS_ID);
    if (!scriptContainer || !partialNavCore || typeof partialNavCore.runPageScripts !== "function") {
      return;
    }
    await partialNavCore.runPageScripts({
      scriptContainer,
      documentObj: document,
      currentUrl: window.location.href,
      loadedScriptUrls,
      executeInlineScript,
      signal,
    });
  }

  function updatePageMeta(nextDocument) {
    document.title = "春秋乱世庄园主";

    const nextCsrfMeta = nextDocument.querySelector('meta[name="csrf-token"]');
    const currentCsrfMeta = document.querySelector('meta[name="csrf-token"]');
    if (nextCsrfMeta && currentCsrfMeta) {
      currentCsrfMeta.setAttribute("content", nextCsrfMeta.getAttribute("content") || "");
    }
  }

  function restoreScroll(targetUrl) {
    if (targetUrl.hash) {
      const targetId = decodeURIComponent(targetUrl.hash.slice(1));
      const anchorEl = document.getElementById(targetId);
      if (anchorEl) {
        anchorEl.scrollIntoView({ behavior: "auto", block: "start" });
        return;
      }
    }
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  }

  async function navigate(url, options) {
    const targetUrl = new URL(url, window.location.href);
    if (targetUrl.origin !== window.location.origin) {
      window.location.href = targetUrl.href;
      return;
    }
    if (!isAllowedPartialUrl(targetUrl.href)) {
      window.location.href = targetUrl.href;
      return;
    }

    if (activeNavigationController) {
      activeNavigationController.abort();
    }
    const navigationController = new window.AbortController();
    activeNavigationController = navigationController;
    const { signal } = navigationController;
    const currentRequestSeq = ++requestSeq;
    try {
      const fetchResult = await fetchDocument(targetUrl.href, signal);
      if (currentRequestSeq !== requestSeq) {
        return;
      }

      const hardNavigationUrl = responseHardNavigationUrl(targetUrl, fetchResult);
      if (hardNavigationUrl) {
        window.location.href = hardNavigationUrl;
        return;
      }

      const nextDocument = fetchResult.document;
      if (!isAuthenticatedDocument(nextDocument)) {
        window.location.href = fetchResult.finalUrl || targetUrl.href;
        return;
      }

      const replaced = replaceCoreSections(nextDocument);
      if (!replaced) {
        window.location.href = targetUrl.href;
        return;
      }

      syncExtraHead(nextDocument);
      updatePageMeta(nextDocument);
      await executePageScripts(nextDocument, signal);
      if (currentRequestSeq !== requestSeq) {
        return;
      }

      if (!options || options.pushState !== false) {
        window.history.pushState({ partialNav: true }, "", targetUrl.href);
      }

      restoreScroll(targetUrl);
      document.dispatchEvent(new CustomEvent("partial-nav:loaded", { detail: { url: targetUrl.href } }));
    } catch (error) {
      if (signal.aborted || currentRequestSeq !== requestSeq || (error && error.name === "AbortError")) {
        return;
      }
      console.error("[partial-nav] navigation failed", error);
      if (currentRequestSeq === requestSeq) {
        window.location.href = targetUrl.href;
      }
    } finally {
      if (activeNavigationController === navigationController) {
        activeNavigationController = null;
      }
    }
  }

  document.addEventListener("click", (event) => {
    const link = event.target.closest(NAV_LINK_SELECTOR);
    if (!shouldHandleClick(event, link)) {
      return;
    }

    event.preventDefault();
    navigate(link.href, { pushState: true });
  });

  window.addEventListener("popstate", () => {
    navigate(window.location.href, { pushState: false });
  });
})();
