(function () {
  const COLLAPSE_STORAGE_KEY = "manor-collapse-states";

  function getCollapseStates() {
    try {
      return JSON.parse(window.localStorage.getItem(COLLAPSE_STORAGE_KEY) || "{}");
    } catch (error) {
      console.warn("读取首页折叠状态失败:", error);
      return {};
    }
  }

  function saveCollapseStates(states) {
    try {
      window.localStorage.setItem(COLLAPSE_STORAGE_KEY, JSON.stringify(states));
    } catch (error) {
      console.warn("保存首页折叠状态失败:", error);
    }
  }

  function getCSRFToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    const metaToken = meta ? meta.getAttribute("content") : "";
    if (metaToken && metaToken !== "NOTPROVIDED") {
      return metaToken;
    }

    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (input && input.value) {
      return input.value;
    }

    const cookie = document.cookie
      .split("; ")
      .find((row) => row.startsWith("csrftoken="));
    return cookie ? decodeURIComponent(cookie.split("=")[1]) : "";
  }

  async function confirmAction(message, options) {
    if (typeof window.gameConfirm === "function") {
      return window.gameConfirm(message, options);
    }
    return window.confirm(message);
  }

  function initMobileCollapses() {
    const isMobile = () => window.innerWidth <= 768;
    const states = getCollapseStates();

    document.querySelectorAll('[data-toggle="collapse"]').forEach((trigger) => {
      const targetId = trigger.dataset.target;
      const target = targetId ? document.getElementById(targetId) : null;
      if (!target) {
        return;
      }

      if (isMobile()) {
        const shouldCollapse = states[targetId] !== undefined ? states[targetId] : true;
        if (shouldCollapse) {
          trigger.classList.add("collapsed");
          target.classList.add("collapsed");
        }
      }

      trigger.addEventListener("click", () => {
        if (!isMobile()) {
          return;
        }

        const collapsed = trigger.classList.toggle("collapsed");
        target.classList.toggle("collapsed", collapsed);
        states[targetId] = collapsed;
        saveCollapseStates(states);
      });
    });
  }

  function initScoutRetreatForms() {
    document.querySelectorAll(".scout-retreat-form").forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        form.dataset.confirming = "true";
        form.dataset.actionPending = "1";
        let actionSubmitted = false;
        try {
          const confirmed = await confirmAction("确定要撤退侦察吗？探子将立即返程。", { title: "撤退确认" });
          if (confirmed) {
            form.submit();
            actionSubmitted = true;
          }
        } catch (error) {
          console.error("撤退确认失败:", error);
        } finally {
          delete form.dataset.confirming;
          if (!actionSubmitted) {
            delete form.dataset.actionPending;
          }
        }
      });
    });
  }

  function initRaidRetreatButtons() {
    const csrfToken = getCSRFToken();

    document.querySelectorAll("[data-retreat-raid]").forEach((button) => {
      button.addEventListener("click", async () => {
        const retreatUrl = button.dataset.retreatUrl;
        if (!retreatUrl) {
          return;
        }

        button.dataset.confirming = "true";
        let confirmed;
        try {
          confirmed = await confirmAction("确定要撤退吗？部队将立即返程。", { title: "撤退确认" });
        } finally {
          delete button.dataset.confirming;
        }
        if (!confirmed) {
          return;
        }

        button.dataset.actionPending = "1";
        button.disabled = true;
        button.textContent = "撤退中...";

        try {
          const response = await fetch(retreatUrl, {
            method: "POST",
            headers: csrfToken ? { "X-CSRFToken": csrfToken } : {},
          });
          const data = await response.json();
          if (data.success) {
            window.location.reload();
            return;
          }

          window.alert(`撤退失败: ${data.error || "未知错误"}`);
        } catch (error) {
          console.error("撤退请求失败:", error);
          window.alert("请求失败，请稍后重试");
        }

        button.disabled = false;
        button.textContent = "撤退";
        delete button.dataset.actionPending;
      });
    });
  }

  initMobileCollapses();
  initScoutRetreatForms();
  initRaidRetreatButtons();
})();
