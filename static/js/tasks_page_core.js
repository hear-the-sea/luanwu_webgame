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

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    buildTaskTabUrl,
  };
}

if (typeof window !== "undefined") {
  window.TasksPageCore = {
    buildTaskTabUrl,
  };
}
