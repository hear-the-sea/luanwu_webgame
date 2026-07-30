(function () {
  if (window.__productionCancelHandlerReady) {
    return;
  }
  window.__productionCancelHandlerReady = true;

  document.addEventListener("submit", async (event) => {
    const form = event.target.closest?.(".js-production-cancel-form");
    if (!form || form.dataset.confirmed === "true") {
      return;
    }

    event.preventDefault();
    if (form.dataset.confirming === "true") {
      return;
    }
    form.dataset.confirming = "true";

    const message = form.dataset.confirmMessage || "取消后已消耗的材料不会返还。确定取消吗？";
    const options = {
      title: form.dataset.confirmTitle || "取消生产",
      okText: form.dataset.confirmOkText || "确认取消",
      cancelText: "返回",
    };

    try {
      const confirmed =
        window.gameDialog && typeof window.gameDialog.danger === "function"
          ? await window.gameDialog.danger(message, options)
          : window.confirm(message);
      if (confirmed) {
        form.dataset.confirmed = "true";
        form.submit();
      }
    } finally {
      delete form.dataset.confirming;
    }
  });
})();
