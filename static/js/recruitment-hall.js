(() => {
  const CHUNK_SIZE = 96;
  const CHUNK_RENDER_THRESHOLD = 160;
  const recruitmentHallCore = window.RecruitmentHallCore;

  let selectedIds = new Set();
  let candidates = [];
  let candidateObserver = null;

  const confirmDialog = async (message, options = {}) => {
    if (typeof window.gameConfirm === "function") {
      return window.gameConfirm(message, options);
    }
    return Promise.resolve(window.confirm(message));
  };

  const formatDurationCN = (totalSeconds) => {
    const seconds = Math.max(0, Number.parseInt(totalSeconds || 0, 10));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainSeconds = seconds % 60;
    const parts = [];
    if (hours > 0) {
      parts.push(`${hours}小时`);
    }
    if (minutes > 0) {
      parts.push(`${minutes}分钟`);
    }
    if (remainSeconds > 0 || parts.length === 0) {
      parts.push(`${remainSeconds}秒`);
    }
    return parts.join("");
  };

  const showMessage = async (message, level = "success") => {
    if (!message) {
      return;
    }
    if (!window.gameDialog) {
      window.alert(message);
      return;
    }
    if (level === "warning" && typeof window.gameDialog.warning === "function") {
      await window.gameDialog.warning(message, { title: "提示" });
      return;
    }
    if (level === "error") {
      await window.gameDialog.error(message, { title: "错误" });
      return;
    }
    await window.gameDialog.success(message, { title: "提示" });
  };

  const parseCandidatesPayload = () => {
    const rawCandidates = document.getElementById("recruit-candidates-data");
    try {
      const parsed = JSON.parse(rawCandidates?.textContent || "[]");
      return Array.isArray(parsed) ? parsed : [];
    } catch (_error) {
      return [];
    }
  };

  const isRarityShown = (candidate) => {
    return Boolean(candidate?.rarity_revealed) || candidate?.rarity === "gray" || candidate?.rarity === "red";
  };

  const replaceSelectedIds = (ids) => {
    selectedIds = new Set(recruitmentHallCore.normalizeSelectedIds(ids));
  };

  const syncSelectedCandidateInputs = () => {
    const hiddenContainer = document.getElementById("candidate-selected-ids");
    if (!hiddenContainer) {
      return;
    }
    hiddenContainer.textContent = "";
    recruitmentHallCore.normalizeSelectedIds(selectedIds).forEach((id) => {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "candidate_ids";
      input.value = String(id);
      hiddenContainer.appendChild(input);
    });
  };

  const updateCandidateEmptyHint = () => {
    const emptyHint = document.getElementById("candidate-empty-hint");
    if (!emptyHint) {
      return;
    }
    emptyHint.style.display = candidates.length > 0 ? "none" : "";
  };

  const buildCandidateNode = (candidate, updateRenderProgress) => {
    const label = document.createElement("label");
    label.className =
      "flex items-center gap-2 px-3 py-2 bg-bg-hover rounded-md cursor-pointer hover:bg-bg-secondary border border-border-light";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "cursor-pointer";
    checkbox.value = String(candidate.id);
    checkbox.dataset.candidateId = String(candidate.id);
    checkbox.checked = selectedIds.has(candidate.id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        replaceSelectedIds([...selectedIds, candidate.id]);
      } else {
        selectedIds.delete(candidate.id);
        replaceSelectedIds(selectedIds);
      }
      syncSelectedCandidateInputs();
      updateRenderProgress();
    });

    const nameSpan = document.createElement("span");
    nameSpan.className = "font-semibold";
    if (isRarityShown(candidate)) {
      nameSpan.className += ` rarity-text-${candidate.rarity}`;
    }
    nameSpan.textContent = candidate.display_name || "未知门客";

    label.appendChild(checkbox);
    label.appendChild(nameSpan);
    return label;
  };

  const syncRenderedSelections = (candidateList) => {
    if (!candidateList) {
      return;
    }
    candidateList.querySelectorAll("input[data-candidate-id]").forEach((checkbox) => {
      checkbox.checked = selectedIds.has(Number(checkbox.dataset.candidateId));
    });
  };

  const getCandidateCount = () => {
    const panel = document.getElementById("recruit-candidates-section");
    const parsed = Number.parseInt(panel?.dataset?.candidateCount || "0", 10);
    if (!Number.isFinite(parsed) || parsed <= 0) {
      return 0;
    }
    return parsed;
  };

  const initCandidateList = () => {
    if (candidateObserver) {
      candidateObserver.disconnect();
      candidateObserver = null;
    }

    replaceSelectedIds([]);
    candidates = parseCandidatesPayload();
    syncSelectedCandidateInputs();

    const candidateList = document.getElementById("candidate-list");
    const renderProgress = document.getElementById("candidate-render-progress");
    const loadMoreBtn = document.getElementById("candidate-load-more");
    const sentinel = document.getElementById("candidate-sentinel");

    updateCandidateEmptyHint();
    if (!candidateList) {
      return;
    }
    candidateList.textContent = "";

    let renderedCount = 0;
    const useChunkedRender = recruitmentHallCore.shouldUseChunkedRender(candidates.length, CHUNK_RENDER_THRESHOLD);
    const stepSize = useChunkedRender ? CHUNK_SIZE : Math.max(candidates.length, 1);
    const updateRenderProgress = () => {
      if (!renderProgress) {
        return;
      }
      renderProgress.textContent = recruitmentHallCore.buildRenderProgressText({
        total: candidates.length,
        rendered: renderedCount,
        selected: selectedIds.size,
        chunked: useChunkedRender,
      });
    };

    const renderNextChunk = () => {
      const nextCount = Math.min(candidates.length, renderedCount + stepSize);
      if (nextCount <= renderedCount) {
        if (loadMoreBtn) {
          loadMoreBtn.style.display = "none";
          loadMoreBtn.disabled = true;
        }
        updateRenderProgress();
        return;
      }

      const fragment = document.createDocumentFragment();
      for (let index = renderedCount; index < nextCount; index += 1) {
        fragment.appendChild(buildCandidateNode(candidates[index], updateRenderProgress));
      }
      candidateList.appendChild(fragment);
      renderedCount = nextCount;
      if (loadMoreBtn && renderedCount >= candidates.length) {
        loadMoreBtn.style.display = "none";
        loadMoreBtn.disabled = true;
      }
      updateRenderProgress();
    };

    if (candidates.length > 0) {
      renderNextChunk();
    } else if (loadMoreBtn) {
      loadMoreBtn.style.display = "none";
      loadMoreBtn.disabled = true;
    }

    if (loadMoreBtn && loadMoreBtn.dataset.ajaxBound !== "1") {
      loadMoreBtn.dataset.ajaxBound = "1";
      loadMoreBtn.addEventListener("click", renderNextChunk);
    }

    if (useChunkedRender && sentinel && "IntersectionObserver" in window) {
      candidateObserver = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (entry.isIntersecting) {
              renderNextChunk();
            }
          });
        },
        { rootMargin: "200px 0px 200px 0px" }
      );
      candidateObserver.observe(sentinel);
    }

    const selectButtons = [
      document.getElementById("candidate-select-all"),
      document.getElementById("candidate-select-all-top"),
    ].filter(Boolean);
    selectButtons.forEach((button) => {
      if (button.dataset.ajaxBound === "1") {
        return;
      }
      button.dataset.ajaxBound = "1";
      button.addEventListener("click", () => {
        replaceSelectedIds([
          ...selectedIds,
          ...candidates.map((candidate) => candidate.id),
        ]);
        syncSelectedCandidateInputs();
        syncRenderedSelections(candidateList);
        updateRenderProgress();
      });
    });

    updateRenderProgress();
  };

  const parseJsonResponse = async (response) => {
    const contentType = (response.headers.get("content-type") || "").toLowerCase();
    if (!contentType.includes("application/json")) {
      return null;
    }
    try {
      return await response.json();
    } catch (_error) {
      return null;
    }
  };

  const replaceSectionHtml = (sectionId, html) => {
    if (!html || typeof html !== "string") {
      return;
    }
    const current = document.getElementById(sectionId);
    if (!current) {
      return;
    }
    const wrapper = document.createElement("div");
    wrapper.innerHTML = html.trim();
    const next = wrapper.firstElementChild;
    if (!next) {
      return;
    }
    current.replaceWith(next);
  };

  const applyRecruitmentHallFragments = (data) => {
    replaceSectionHtml("recruit-pools-section", data.hall_pools_html);
    replaceSectionHtml("recruit-candidates-section", data.hall_candidates_html);
    replaceSectionHtml("recruit-records-section", data.hall_records_html);
    initRecruitHallUI();
  };

  const bindSubmitterFallback = (form) => {
    if (!form || form.dataset.submitterFallbackBound === "1") {
      return;
    }
    form.dataset.submitterFallbackBound = "1";
    form.__lastSubmitter = null;
    form.querySelectorAll("button[type='submit']").forEach((button) => {
      button.addEventListener("click", () => {
        form.__lastSubmitter = button;
      });
    });
  };

  const resolveSubmitter = (form, event) => {
    const eventSubmitter = event && "submitter" in event ? event.submitter : null;
    if (eventSubmitter) {
      return eventSubmitter;
    }

    const fallbackSubmitter = form.__lastSubmitter;
    if (fallbackSubmitter && document.body.contains(fallbackSubmitter)) {
      return fallbackSubmitter;
    }

    const active = document.activeElement;
    if (active && active.tagName === "BUTTON" && active.form === form && active.type === "submit") {
      return active;
    }

    return form.querySelector("button[type='submit']");
  };

  const submitAjaxForm = async (form, submitter, options = {}) => {
    const button = submitter || form.querySelector("button[type='submit']");
    const originalText = button ? button.textContent : "";
    const loadingText = options.loadingText || "处理中...";
    if (button) {
      button.disabled = true;
      button.textContent = loadingText;
    }

    try {
      const formData = new FormData(form);
      if (button?.name) {
        formData.append(button.name, button.value);
      }
      if (options.includeSelectedCandidates) {
        selectedIds.forEach((id) => formData.append("candidate_ids", String(id)));
      }

      const response = await fetch(form.action, {
        method: "POST",
        body: formData,
        credentials: "same-origin",
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          Accept: "application/json",
        },
      });

      const data = await parseJsonResponse(response);
      if (!data) {
        window.location.reload();
        return null;
      }
      if (!response.ok || !data.success) {
        throw new Error(data.error || data.message || "操作失败");
      }

      applyRecruitmentHallFragments(data);
      await showMessage(data.message, data.message_level || "success");
      return data;
    } catch (error) {
      await showMessage(error?.message || "请求失败，请重试", "error");
      return null;
    } finally {
      if (button && document.body.contains(button)) {
        button.disabled = false;
        button.textContent = originalText;
      }
      if (form) {
        form.__lastSubmitter = null;
      }
    }
  };

  const bindRecruitForms = () => {
    document.querySelectorAll(".recruit-form").forEach((form) => {
      if (form.dataset.ajaxBound === "1") {
        return;
      }
      form.dataset.ajaxBound = "1";
      bindSubmitterFallback(form);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const submitter = resolveSubmitter(form, event);
        const candidateCount = getCandidateCount();
        if (candidateCount > 0) {
          const confirmedHasCandidates = await confirmDialog(
            "候选区还有待处理的门客，确定要进行新的招募吗？这将清空当前候选区。",
            { title: "招募确认" }
          );
          if (!confirmedHasCandidates) {
            return;
          }
        }

        const poolName = form.dataset.poolName || "招募";
        const costText = form.dataset.poolCost || "免费";
        const baseCount = Number.parseInt(form.dataset.poolBase || "0", 10) || 0;
        const bonusCount = Number.parseInt(form.dataset.poolBonus || "0", 10) || 0;
        const durationSeconds = Number.parseInt(form.dataset.poolDuration || "0", 10) || 0;
        const totalCount = baseCount + bonusCount;
        let countText = `${totalCount}人`;
        if (bonusCount > 0) {
          countText += ` (基础${baseCount} + 酒馆加成${bonusCount})`;
        }

        const confirmed = await confirmDialog(
          `确定要进行 ${poolName} 吗？\n预计招募候选：${countText}\n预计耗时：${formatDurationCN(durationSeconds)}\n本次消耗：${costText}`,
          { title: "招募确认" }
        );
        if (!confirmed) {
          return;
        }

        await submitAjaxForm(form, submitter, { loadingText: "招募中..." });
      });
    });
  };

  const bindMagnifyingGlassForms = () => {
    document.querySelectorAll(".magnifying-glass-form").forEach((form) => {
      if (form.dataset.ajaxBound === "1") {
        return;
      }
      form.dataset.ajaxBound = "1";
      bindSubmitterFallback(form);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const submitter = resolveSubmitter(form, event);
        await submitAjaxForm(form, submitter, { loadingText: "使用中..." });
      });
    });
  };

  const bindCandidateActionForms = () => {
    document.querySelectorAll(".candidate-action-form").forEach((form) => {
      if (form.dataset.ajaxBound === "1") {
        return;
      }
      form.dataset.ajaxBound = "1";
      form.addEventListener("submit", async (event) => {
        const scope = form.querySelector("input[name='scope']")?.value || "selected";
        if (scope !== "selected") {
          return;
        }

        syncSelectedCandidateInputs();
        if (selectedIds.size <= 0) {
          event.preventDefault();
          await showMessage("请先勾选候选门客。", "warning");
        }
      });
    });
  };

  function initRecruitHallUI() {
    if (!document.getElementById("recruit-pools-section")) {
      return;
    }
    initCandidateList();
    bindRecruitForms();
    bindMagnifyingGlassForms();
    bindCandidateActionForms();
  }

  document.addEventListener("DOMContentLoaded", initRecruitHallUI);
  document.addEventListener("partial-nav:loaded", initRecruitHallUI);
})();
