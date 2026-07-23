(function initJailPageCore(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.JailPageCore = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function createJailPageCore() {
  "use strict";

  const signed = (value) => {
    const normalized = Number(value) || 0;
    return normalized > 0 ? `+${normalized}` : String(normalized);
  };

  const formatDeltaSummary = (result, speakerName = "") => {
    const parts = [
      `心防 ${signed(result?.heart_delta)}`,
      `归心 ${signed(result?.affinity_delta)}`,
    ];
    const speakerDelta = Number(result?.speaker_loyalty_delta) || 0;
    if (speakerDelta !== 0) {
      parts.push(`${speakerName || "说客"} 忠诚 ${signed(speakerDelta)}`);
    }
    return parts.join("｜");
  };

  const formatRecruitmentSummary = (payload) => {
    if (payload?.recruited !== true) {
      return "";
    }
    const loyalty = Number(payload?.initial_loyalty);
    if (!Number.isFinite(loyalty)) {
      return "";
    }
    return `已成为 1 级门客｜初始忠诚 ${loyalty}`;
  };

  const buildInteractionPayload = (method, rawSpeakerId) => {
    const payload = { method: String(method || "") };
    if (method === "reason" || method === "might") {
      const speakerId = Number.parseInt(String(rawSpeakerId || ""), 10);
      if (Number.isInteger(speakerId) && speakerId > 0) {
        payload.speaker_id = speakerId;
      }
    }
    return payload;
  };

  const formatHistoryEntries = (entries) => {
    const normalized = (Array.isArray(entries) ? entries : [])
      .map((entry) => String(entry || "").replace(/\s+/g, " ").trim())
      .filter(Boolean);
    if (!normalized.length) {
      return "尚无招降记录。";
    }
    return normalized.map((entry, index) => `${index + 1}. ${entry}`).join("\n\n");
  };

  return {
    formatDeltaSummary,
    formatRecruitmentSummary,
    buildInteractionPayload,
    formatHistoryEntries,
  };
});
