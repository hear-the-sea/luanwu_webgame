(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
    return;
  }
  root.PartialNavCore = factory();
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function loadExternalScript(documentObj, absoluteSrc) {
    return new Promise((resolve, reject) => {
      const script = documentObj.createElement("script");
      script.src = absoluteSrc;
      script.async = false;
      script.onload = () => resolve();
      script.onerror = () => reject(new Error(`failed to load script: ${absoluteSrc}`));
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

    const scripts = Array.from(scriptContainer.querySelectorAll("script"));
    for (const scriptEl of scripts) {
      const src = scriptEl.getAttribute("src");
      if (src) {
        const absoluteSrc = new URL(src, currentUrl).href;
        if (loadedScriptUrls.has(absoluteSrc)) {
          continue;
        }

        loadedScriptUrls.add(absoluteSrc);
        try {
          await loadExternalScriptFn(documentObj, absoluteSrc);
        } catch (error) {
          loadedScriptUrls.delete(absoluteSrc);
          throw error;
        }
        continue;
      }

      const code = scriptEl.textContent || "";
      if (code.trim()) {
        executeInlineScript(code);
      }
    }
  }

  return {
    loadExternalScript,
    runPageScripts,
  };
});
