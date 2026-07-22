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

  const classifySpeakerRatio = (rawRatio) => {
    const ratio = Number.isFinite(Number(rawRatio)) ? Number(rawRatio) : 0;
    if (ratio < 0.7) {
      return { kind: "backfire", label: "压倒性劣势" };
    }
    if (ratio < 0.85) {
      return { kind: "failed", label: "明显劣势" };
    }
    if (ratio < 1.15) {
      return { kind: "even", label: "势均力敌" };
    }
    if (ratio < 1.5) {
      return { kind: "advantage", label: "占据优势" };
    }
    return { kind: "dominant", label: "压倒性优势" };
  };

  const buildSpeakerWarning = (method, ratio, speakerName) => {
    const tier = classifySpeakerRatio(ratio);
    if (tier.kind === "backfire") {
      const attribute = method === "might" ? "基础武力" : "基础智力";
      const action = method === "might" ? "强行立威" : "强行辩说";
      return {
        ...tier,
        requiresConfirmation: true,
        message: `${speakerName || "该门客"}的${attribute}明显低于囚徒，${action}将使对方更加抵触，并使说客忠诚下降 1 点（最低为 0）。`,
      };
    }
    if (tier.kind === "failed") {
      return {
        ...tier,
        requiresConfirmation: true,
        message: `${speakerName || "该门客"}与囚徒差距明显，本次必定无法奏效，但仍会消耗囚徒和说客的今日次数。`,
      };
    }
    return { ...tier, requiresConfirmation: false, message: "" };
  };

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

  return {
    classifySpeakerRatio,
    buildSpeakerWarning,
    formatDeltaSummary,
    formatRecruitmentSummary,
    buildInteractionPayload,
  };
});
