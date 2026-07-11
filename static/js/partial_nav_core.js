(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
    return;
  }
  root.PartialNavCore = factory();
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function createAbortError() {
    const error = new Error("partial navigation aborted");
    error.name = "AbortError";
    return error;
  }

  function throwIfAborted(signal) {
    if (signal && signal.aborted) {
      throw createAbortError();
    }
  }

  function loadExternalScript(documentObj, absoluteSrc, signal) {
    return new Promise((resolve, reject) => {
      if (signal && signal.aborted) {
        reject(createAbortError());
        return;
      }

      const script = documentObj.createElement("script");
      script.src = absoluteSrc;
      script.async = false;

      let settled = false;
      const cleanup = () => {
        if (signal && typeof signal.removeEventListener === "function") {
          signal.removeEventListener("abort", handleAbort);
        }
        script.onload = null;
        script.onerror = null;
      };
      const finish = (callback) => {
        if (settled) return;
        settled = true;
        cleanup();
        callback();
      };
      const handleAbort = () => {
        if (typeof script.remove === "function") {
          script.remove();
        }
        finish(() => reject(createAbortError()));
      };

      script.onload = () => {
        if (signal && signal.aborted) {
          handleAbort();
          return;
        }
        finish(resolve);
      };
      script.onerror = () => finish(() => reject(new Error(`failed to load script: ${absoluteSrc}`)));
      if (signal && typeof signal.addEventListener === "function") {
        signal.addEventListener("abort", handleAbort, { once: true });
      }
      documentObj.body.appendChild(script);
    });
  }

  async function runPageScripts(options) {
    const scriptContainer = options && options.scriptContainer;
    if (!scriptContainer) {
      return;
    }

    const documentObj = options.documentObj;
    const currentUrl = options.currentUrl;
    const loadedScriptUrls = options.loadedScriptUrls;
    const executeInlineScript = options.executeInlineScript;
    const loadExternalScriptFn = options.loadExternalScriptFn || loadExternalScript;
    const signal = options.signal;

    const scripts = Array.from(scriptContainer.querySelectorAll("script"));
    for (const scriptEl of scripts) {
      throwIfAborted(signal);
      const src = scriptEl.getAttribute("src");
      if (src) {
        const absoluteSrc = new URL(src, currentUrl).href;
        if (loadedScriptUrls.has(absoluteSrc)) {
          continue;
        }

        loadedScriptUrls.add(absoluteSrc);
        try {
          await loadExternalScriptFn(documentObj, absoluteSrc, signal);
          throwIfAborted(signal);
        } catch (error) {
          loadedScriptUrls.delete(absoluteSrc);
          throw error;
        }
        continue;
      }

      const code = scriptEl.textContent || "";
      if (code.trim()) {
        throwIfAborted(signal);
        executeInlineScript(code);
      }
    }
  }

  return {
    loadExternalScript,
    runPageScripts,
  };
});
