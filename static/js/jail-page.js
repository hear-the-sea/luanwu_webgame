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

  const confirmAction = (message, title, okText = "确认执行") => {
    if (window.gameConfirm) {
      return window.gameConfirm(message, { title, okText });
    }
    if (window.gameDialog?.confirm) {
      return window.gameDialog.confirm(message, { title, okText });
    }
    return Promise.resolve(window.confirm(message));
  };

  const confirmRelease = async (form) => {
    const dossier = form.closest("[data-prisoner-id]");
    const submitButton = form.querySelector('button[type="submit"]');
    if (form.dataset.releaseConfirmPending === "1" || dossier?.dataset.jailActionPending === "1") {
      return;
    }
    form.dataset.releaseConfirmPending = "1";
    if (dossier) {
      dossier.dataset.jailActionPending = "1";
    }

    let submitted = false;
    try {
      const prisonerName = form.dataset.releasePrisonerName || "该俘虏";
      const accepted = await confirmAction(
        `确定释放“${prisonerName}”吗？释放后无法撤回。`,
        "确认释放",
        "确认释放"
      );
      if (!accepted) {
        return;
      }

      form.submit();
      submitted = true;
    } catch (error) {
      await showError(error instanceof Error ? error.message : "操作失败，请稍后重试");
    } finally {
      if (!submitted) {
        delete form.dataset.releaseConfirmPending;
        if (dossier) {
          delete dossier.dataset.jailActionPending;
        }
        submitButton?.focus();
      }
    }
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
      : payload?.recruited === true
        ? "归附完成"
        : payload?.recruited === false
          ? "归附未成"
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
    };
  };

  const showHistory = async (button) => {
    const dossier = button.closest("[data-prisoner-id]");
    if (!dossier) {
      return;
    }
    const entries = Array.from(dossier.querySelectorAll("[data-jail-history-item]"), (item) => item.textContent || "");
    const message = core.formatHistoryEntries(entries);
    const title = `${button.dataset.prisonerName || "俘虏"} · 招降记录`;
    if (window.gameDialog?.alert) {
      await window.gameDialog.alert(message, { title });
      return;
    }
    window.alert(`${title}\n\n${message}`);
  };

  const setDossierPending = (dossier) => {
    const controls = Array.from(dossier.querySelectorAll("button"));
    const disabledStates = controls.map((control) => [control, control.disabled]);
    dossier.classList.add("jail-action-pending");
    controls.forEach((control) => {
      control.disabled = true;
    });
    return () => {
      dossier.classList.remove("jail-action-pending");
      disabledStates.forEach(([control, disabled]) => {
        control.disabled = disabled;
      });
    };
  };

  const runAction = async (button) => {
    const action = button.dataset.jailAction;
    const dossier = button.closest("[data-prisoner-id]");
    if (!action || !dossier || !button.dataset.actionUrl || dossier.dataset.jailActionPending === "1") {
      return;
    }
    dossier.dataset.jailActionPending = "1";

    let restoreControls = null;
    let completed = false;
    try {
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
      } else if (action === "milestone") {
        payload = { choice: button.dataset.choice || "" };
      } else if (action === "recruit") {
        payload = { mode: button.dataset.mode || "standard" };
        const accepted = await confirmAction(
          "无论成败都会消耗所示金条，并占用该囚徒今日的归附尝试。",
          "确认归附"
        );
        if (!accepted) {
          return;
        }
      }

      restoreControls = setDossierPending(dossier);
      const result = await postJson(button.dataset.actionUrl, payload);
      await finishAndReload(result, speakerName);
      completed = true;
    } catch (error) {
      await showError(error instanceof Error ? error.message : "操作失败，请稍后重试");
    } finally {
      if (!completed) {
        restoreControls?.();
        delete dossier.dataset.jailActionPending;
        button.focus();
      }
    }
  };

  root.addEventListener("click", (event) => {
    const historyButton = event.target.closest("button[data-jail-history-open]");
    if (historyButton) {
      event.preventDefault();
      const dossier = historyButton.closest("[data-prisoner-id]");
      if (dossier?.dataset.jailActionPending === "1") {
        return;
      }
      void showHistory(historyButton);
      return;
    }

    const button = event.target.closest("button[data-jail-action]");
    if (!button || button.disabled) {
      return;
    }
    event.preventDefault();
    void runAction(button);
  });

  root.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-jail-release-form]");
    if (!form) {
      return;
    }
    event.preventDefault();
    const dossier = form.closest("[data-prisoner-id]");
    if (dossier?.dataset.jailActionPending === "1") {
      return;
    }
    void confirmRelease(form);
  });
})();
