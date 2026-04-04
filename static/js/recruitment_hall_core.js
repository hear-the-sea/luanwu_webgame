(function (root, factory) {
  const api = factory();

  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }

  root.RecruitmentHallCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function parseInteger(value, fallback = 0) {
    const parsed = Number.parseInt(String(value || ""), 10);
    return Number.isNaN(parsed) ? fallback : parsed;
  }

  function shouldUseChunkedRender(total, threshold) {
    return parseInteger(total, 0) > parseInteger(threshold, 0);
  }

  function buildRenderProgressText(state) {
    const total = Math.max(0, parseInteger(state && state.total, 0));
    const rendered = Math.max(0, parseInteger(state && state.rendered, 0));
    const selected = Math.max(0, parseInteger(state && state.selected, 0));
    const chunked = Boolean(state && state.chunked);

    if (total <= 0) {
      return "";
    }

    if (chunked) {
      return `已加载 ${Math.min(rendered, total)}/${total}，已勾选 ${selected}`;
    }

    return `共 ${total} 名候选，已勾选 ${selected}`;
  }

  function normalizeSelectedIds(ids) {
    return Array.from(ids || [])
      .map((value) => parseInteger(value, 0))
      .filter((value) => value > 0)
      .filter((value, index, values) => values.indexOf(value) === index)
      .sort((left, right) => left - right);
  }

  return {
    buildRenderProgressText,
    normalizeSelectedIds,
    shouldUseChunkedRender,
  };
});
