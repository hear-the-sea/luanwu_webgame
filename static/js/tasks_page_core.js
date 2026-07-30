function buildTaskTabUrl(currentHref, tabId) {
  const url = new URL(currentHref);
  url.searchParams.delete("mission");
  if (tabId) {
    url.searchParams.set("tab", tabId);
  } else {
    url.searchParams.delete("tab");
  }
  return url.toString();
}

function buildMissionCardConfirmation({ missionName, cardCount, usedCount, dailyLimit } = {}) {
  const resolvedMissionName = String(missionName || "").trim() || "当前任务";
  const resolveCount = (value, fallback) => {
    const parsed = Number.parseInt(value, 10);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
  };
  const resolvedCardCount = resolveCount(cardCount, 0);
  const resolvedUsedCount = resolveCount(usedCount, 0);
  const resolvedDailyLimit = Math.max(1, resolveCount(dailyLimit, 5));

  return `确定消耗 1 张任务卡，为「${resolvedMissionName}」增加 1 次今日挑战次数吗？当前持有 ${resolvedCardCount} 张，该任务今日已使用 ${resolvedUsedCount} / ${resolvedDailyLimit} 张。`;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    buildTaskTabUrl,
    buildMissionCardConfirmation,
  };
}

if (typeof window !== "undefined") {
  window.TasksPageCore = {
    buildTaskTabUrl,
    buildMissionCardConfirmation,
  };
}
