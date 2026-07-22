(() => {
  "use strict";

  const root = document.querySelector('[data-jail-root="1"]');
  const core = window.JailPageCore;
  if (!root || !core || root.dataset.bound === "1") {
    return;
  }
  root.dataset.bound = "1";

  const csrfToken = () => {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta?.content) {
      return meta.content;
    }
    return document.querySelector('input[name="csrfmiddlewaretoken"]')?.value || "";
  };

  const showError = (message) => {
    if (window.gameDialog?.error) {
      return window.gameDialog.error(message, { title: "操作未完成" });
    }
    window.alert(message);
    return Promise.resolve();
  };

  const showSuccess = (message, title = "招降结果") => {
    if (window.gameDialog?.success) {
      return window.gameDialog.success(message, { title });
    }
    window.alert(message);
    return Promise.resolve();
  };

  const confirmAction = (message, title) => {
    if (window.gameConfirm) {
      return window.gameConfirm(message, { title, okText: "确认执行" });
    }
    if (window.gameDialog?.confirm) {
      return window.gameDialog.confirm(message, { title, okText: "确认执行" });
    }
    return Promise.resolve(window.confirm(message));
  };

  const postJson = async (url, payload) => {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
      },
      body: JSON.stringify(payload || {}),
    });
    const data = await response.json().catch(() => null);
    if (!response.ok || !data?.success) {
      throw new Error(data?.error || "操作失败，请稍后重试");
    }
    return data;
  };

  const finishAndReload = async (payload, speakerName = "") => {
    const result = payload?.result;
    const story = result?.text || payload?.text || payload?.message || "操作已完成";
    const summary = result
      ? core.formatDeltaSummary(result, speakerName)
      : core.formatRecruitmentSummary(payload);
    const title = result?.outcome === "event"
      ? "归心事件"
      : payload?.initial_loyalty != null
        ? "归附完成"
        : "招降结果";
    await showSuccess(summary ? `${story}\n${summary}` : story, title);
    window.location.reload();
  };

  const selectedSpeaker = (dossier, method) => {
    const select = dossier.querySelector(`[data-speaker-select="${method}"]`);
    const option = select?.selectedOptions?.[0];
    if (!select?.value || !option) {
      return null;
    }
    return {
      id: select.value,
      name: option.dataset.speakerName || option.textContent.trim(),
      ratio: Number(option.dataset.ratio || 0),
    };
  };

  const runAction = async (button) => {
    const action = button.dataset.jailAction;
    const dossier = button.closest("[data-prisoner-id]");
    if (!action || !dossier || !button.dataset.actionUrl) {
      return;
    }

    let payload = {};
    let speakerName = "";
    if (action === "interact") {
      const method = button.dataset.method || "";
      const speaker = method === "reason" || method === "might" ? selectedSpeaker(dossier, method) : null;
      if ((method === "reason" || method === "might") && !speaker) {
        await showError("请选择一名可用说客");
        return;
      }
      payload = core.buildInteractionPayload(method, speaker?.id || "");
      speakerName = speaker?.name || "";
      if (speaker) {
        const warning = core.buildSpeakerWarning(method, speaker.ratio, speaker.name);
        if (warning.requiresConfirmation && !(await confirmAction(warning.message, warning.label))) {
          return;
        }
      }
    } else if (action === "milestone") {
      payload = { choice: button.dataset.choice || "" };
    } else if (action === "recruit") {
      payload = { mode: button.dataset.mode || "standard" };
      const accepted = await confirmAction("归附后将按当前方式生成 1 级门客，并消耗所示金条。", "确认归附");
      if (!accepted) {
        return;
      }
    }

    dossier.classList.add("jail-action-pending");
    button.disabled = true;
    try {
      const result = await postJson(button.dataset.actionUrl, payload);
      await finishAndReload(result, speakerName);
    } catch (error) {
      dossier.classList.remove("jail-action-pending");
      button.disabled = false;
      await showError(error instanceof Error ? error.message : "操作失败，请稍后重试");
    }
  };

  root.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-jail-action]");
    if (!button || button.disabled) {
      return;
    }
    event.preventDefault();
    void runAction(button);
  });
})();
