(function () {
  "use strict";

  function alphaContains(mask, u, v) {
    if (!mask || u < 0 || v < 0 || u >= 1 || v >= 1) return false;
    const x = Math.min(mask.width - 1, Math.floor(u * mask.width));
    const y = Math.min(mask.height - 1, Math.floor(v * mask.height));
    return mask.data[(y * mask.width + x) * 4 + 3] >= 24;
  }

  function hitNode(records, x, y) {
    for (let index = records.length - 1; index >= 0; index -= 1) {
      const record = records[index];
      const frame = record.node.frame;
      if (alphaContains(record.mask, (x - frame.x) / frame.width, (y - frame.y) / frame.height)) {
        return record;
      }
    }
    return null;
  }

  function selectionError(count, minimum, maximum) {
    if (count < minimum) return minimum === maximum ? `请选择 ${minimum} 名人物。` : `请至少选择 ${minimum} 名门客。`;
    if (count > maximum) return `最多选择 ${maximum} 名。`;
    return "";
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { alphaContains, hitNode, selectionError };
  }
  if (typeof document === "undefined") return;

  function initializeSeasonPage() {
    const root = document.querySelector("[data-season-page]");
    if (!root || root.dataset.initialized === "1") return;
    if (typeof window.seasonPageCleanup === "function") window.seasonPageCleanup();
    root.dataset.initialized = "1";

    const controller = new AbortController();
    const { signal } = controller;
    const overlays = Array.from(root.querySelectorAll(".season-overlay"));
    const forms = Array.from(root.querySelectorAll("[data-season-form]"));
    overlays.forEach((overlay) => document.body.appendChild(overlay));
    [root, ...overlays].forEach(scope => scope.querySelectorAll(".season-portrait img").forEach(portrait => {
      const showSilhouette = () => { portrait.hidden = true; };
      portrait.addEventListener("error", showSilhouette, { signal });
      if (portrait.complete && !portrait.naturalWidth) showSilhouette();
    }));
    // 阵营人物五角星：提示框挂到 body 并使用 fixed 定位，避免被门客列表的滚动容器裁切。
    const factionTip = document.createElement("div");
    factionTip.className = "season-faction-tip";
    factionTip.setAttribute("role", "tooltip");
    factionTip.hidden = true;
    document.body.appendChild(factionTip);
    let activeFactionStar = null;
    const closeFactionTip = () => {
      if (!activeFactionStar) return;
      activeFactionStar.setAttribute("aria-expanded", "false");
      activeFactionStar = null;
      factionTip.hidden = true;
    };
    const openFactionTip = (star) => {
      closeFactionTip();
      const heading = document.createElement("strong");
      heading.textContent = "本阵营人物";
      const body = document.createElement("span");
      body.textContent = star.dataset.seasonFactionTip || "";
      const note = document.createElement("small");
      note.textContent = star.dataset.seasonFactionNote || "";
      factionTip.replaceChildren(heading, body, note);
      factionTip.hidden = false;
      activeFactionStar = star;
      star.setAttribute("aria-expanded", "true");
      const starRect = star.getBoundingClientRect();
      const tipRect = factionTip.getBoundingClientRect();
      const left = Math.max(8, Math.min(
        starRect.left + starRect.width / 2 - tipRect.width / 2,
        window.innerWidth - tipRect.width - 8,
      ));
      let top = starRect.bottom + 7;
      if (top + tipRect.height > window.innerHeight - 8) {
        top = Math.max(8, starRect.top - tipRect.height - 7);
      }
      factionTip.style.left = `${left}px`;
      factionTip.style.top = `${top}px`;
    };
    [root, ...overlays].forEach(scope => scope.querySelectorAll("[data-season-faction-star]").forEach((star) => {
      star.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (activeFactionStar === star) closeFactionTip();
        else openFactionTip(star);
      }, { signal });
    }));
    document.addEventListener("click", (event) => {
      if (!factionTip.contains(event.target)) closeFactionTip();
    }, { signal });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeFactionTip();
    }, { signal });
    window.addEventListener("scroll", closeFactionTip, { capture: true, signal });
    window.addEventListener("resize", closeFactionTip, { signal });
    window.GuildModal?.refresh();
    overlays.forEach(overlay => overlay.querySelectorAll("[data-season-pane-group]").forEach(group => {
      const tabs = Array.from(group.querySelectorAll("[data-season-pane-tab]"));
      const panes = Array.from(group.querySelectorAll("[data-season-pane]"));
      const selectPane = (key, focus = false) => {
        panes.forEach(pane => { pane.hidden = pane.dataset.seasonPane !== key; });
        tabs.forEach(tab => {
          const selected = tab.dataset.seasonPaneTab === key;
          tab.setAttribute("aria-selected", String(selected));
          tab.tabIndex = selected ? 0 : -1;
          if (selected && focus) tab.focus();
        });
      };
      tabs.forEach((tab, index) => {
        tab.addEventListener("click", () => selectPane(tab.dataset.seasonPaneTab), { signal });
        tab.addEventListener("keydown", event => {
          let next;
          if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
          else if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
          else if (event.key === "Home") next = 0;
          else if (event.key === "End") next = tabs.length - 1;
          else return;
          event.preventDefault(); selectPane(tabs[next].dataset.seasonPaneTab, true);
        }, { signal });
      });
    }));
    const countdowns = [
      ...root.querySelectorAll("[data-season-countdown]"),
      ...overlays.flatMap((overlay) => Array.from(overlay.querySelectorAll("[data-season-countdown]"))),
    ];
    const updateCountdowns = () => countdowns.forEach((element) => {
      const deadline = Date.parse(element.dataset.seasonCountdown);
      if (!Number.isFinite(deadline)) return;
      const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      element.textContent = remaining ? `${Math.floor(remaining / 60)}分${String(remaining % 60).padStart(2, "0")}秒` : "等待结算，请刷新";
    });
    updateCountdowns();
    const countdownTimer = countdowns.length ? window.setInterval(updateCountdowns, 1000) : null;

    const mapData = document.getElementById("season-map-data");
    let maps = [];
    try {
      maps = JSON.parse(mapData?.textContent || "[]");
    } catch (error) {
      root.querySelector(".season-map-panel")?.setAttribute("aria-label", "赛季地图，交互暂未加载，请刷新页面");
    }

    // 地图与排行分屏；旧军务链接改为地图上的队伍弹窗。
    const viewTabs = Array.from(root.querySelectorAll("[data-season-view-tab]"));
    const views = Array.from(root.querySelectorAll("[data-season-view]"));
    const setView = (key, moveFocus = false, remember = false) => {
      if (!views.some(view => view.dataset.seasonView === key)) return;
      views.forEach(view => { view.hidden = view.dataset.seasonView !== key; });
      viewTabs.forEach(tab => {
        const selected = tab.dataset.seasonViewTab === key;
        tab.setAttribute("aria-selected", String(selected));
        tab.tabIndex = selected ? 0 : -1;
        if (selected && moveFocus) tab.focus();
      });
      if (remember) window.history.replaceState(null, "", `#season-view-${key}`);
    };
    const followViewHash = () => {
      const oldArmyHash = ["#season-view-army", "#season-expeditions"].includes(window.location.hash);
      if (oldArmyHash || window.location.hash === "#season-team-status") {
        setView("battle");
        window.GuildModal?.open("season-team-status");
        return;
      }
      if (window.location.hash === "#season-rallies") {
        setView("battle");
        window.GuildModal?.open("season-rallies");
        return;
      }
      if (["#season-rewards", "#season-summon"].includes(window.location.hash)) {
        const id = window.location.hash.slice(1);
        if (document.getElementById(id)) {
          setView("battle");
          window.GuildModal?.open(id);
        }
        return;
      }
      let target;
      try { target = document.getElementById(decodeURIComponent(window.location.hash.slice(1))); }
      catch (error) { return; }
      const view = target?.closest("[data-season-view]");
      if (view && root.contains(view)) setView(view.dataset.seasonView);
    };
    viewTabs.forEach((tab, index) => {
      tab.addEventListener("click", () => setView(tab.dataset.seasonViewTab, false, true), { signal });
      tab.addEventListener("keydown", event => {
        let next;
        if (event.key === "ArrowRight") next = (index + 1) % viewTabs.length;
        else if (event.key === "ArrowLeft") next = (index + viewTabs.length - 1) % viewTabs.length;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = viewTabs.length - 1;
        else return;
        event.preventDefault();
        setView(viewTabs[next].dataset.seasonViewTab, true, true);
      }, { signal });
    });
    [root, ...overlays].forEach(scope => scope.querySelectorAll("[data-season-view-open]").forEach(button => {
      button.addEventListener("click", () => {
        overlays.forEach(overlay => window.GuildModal?.close(overlay));
        setView(button.dataset.seasonViewOpen, true, true);
      }, { signal });
    }));
    window.addEventListener("hashchange", followViewHash, { signal });
    followViewHash();

    const tabs = Array.from(root.querySelectorAll("[data-season-tab]"));
    const regions = Array.from(root.querySelectorAll("[data-season-region]"));
    const storageKey = `season-region-${root.dataset.seasonId}`;
    const setRegion = (key, moveFocus) => {
      if (!tabs.some((tab) => tab.dataset.seasonTab === key)) return;
      tabs.forEach((tab) => {
        const active = tab.dataset.seasonTab === key;
        tab.setAttribute("aria-selected", String(active));
        tab.tabIndex = active ? 0 : -1;
        if (active && moveFocus) tab.focus();
      });
      regions.forEach((region) => { region.hidden = region.dataset.seasonRegion !== key; });
      try { window.localStorage.setItem(storageKey, key); } catch (error) { /* 私密浏览仍可正常切换。 */ }
    };
    let savedRegion = "";
    try { savedRegion = window.localStorage.getItem(storageKey) || ""; } catch (error) { /* 使用默认战区。 */ }
    if (savedRegion) setRegion(savedRegion, false);
    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => setRegion(tab.dataset.seasonTab, false), { signal });
      tab.addEventListener("keydown", (event) => {
        const offsets = { ArrowRight: 1, ArrowLeft: -1 };
        let next;
        if (Object.hasOwn(offsets, event.key)) next = (index + offsets[event.key] + tabs.length) % tabs.length;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = tabs.length - 1;
        else return;
        event.preventDefault();
        setRegion(tabs[next].dataset.seasonTab, true);
      }, { signal });
    });

    const recordSets = [];
    maps.forEach((map) => {
      const stage = Array.from(root.querySelectorAll("[data-season-map]")).find((element) => element.dataset.seasonMap === map.key);
      if (!stage) return;
      const buttons = Array.from(stage.querySelectorAll("[data-season-building]"));
      const records = [];
      recordSets.push(records);
      map.nodes.forEach((node) => {
        const button = buttons.find((element) => element.dataset.seasonBuilding === node.key);
        if (!button) return;
        const frame = node.frame;
        button.style.left = `${frame.x / 14.48}%`;
        button.style.top = `${frame.y / 10.86}%`;
        button.style.width = `${frame.width / 14.48}%`;
        button.style.height = `${frame.height / 10.86}%`;
        button.style.transformOrigin = `${node.anchor.x * 100}% ${node.anchor.y * 100}%`;
        button.style.setProperty("--map-sign-x", `${node.anchor.x * 100}%`);
        button.style.setProperty("--map-sign-y", `${node.anchor.y * 100}%`);
        button.classList.add("is-positioned");
        const image = button.querySelector("img");
        const record = { node, button, mask: null };
        records.push(record);
        const prepareMask = () => {
          if (signal.aborted || !image.naturalWidth || record.mask) return;
          try {
            const canvas = document.createElement("canvas");
            canvas.width = Math.min(320, image.naturalWidth);
            canvas.height = Math.max(1, Math.round(canvas.width * image.naturalHeight / image.naturalWidth));
            const context = canvas.getContext("2d", { willReadFrequently: true });
            if (!context) return;
            context.drawImage(image, 0, 0, canvas.width, canvas.height);
            record.mask = { width: canvas.width, height: canvas.height, data: context.getImageData(0, 0, canvas.width, canvas.height).data };
            canvas.width = 0;
            canvas.height = 0;
          } catch (error) {
            // 图片跨域不可采样时保留键盘和木牌入口，不以矩形代替轮廓。
            record.mask = null;
          }
        };
        image.addEventListener("load", prepareMask, { signal });
        if (image.complete) prepareMask();
      });

      let active = null;
      let pointerStart = null;
      const clearHover = () => {
        active?.button.classList.remove("is-hovered");
        active = null;
        stage.classList.remove("is-pointing");
      };
      const locate = (event) => {
        const sign = event.target.closest?.(".map-wooden-sign");
        if (sign) {
          const button = sign.closest("[data-season-building]");
          return records.find((record) => record.button === button) || null;
        }
        const rect = stage.getBoundingClientRect();
        return hitNode(records, (event.clientX - rect.left) * 1448 / rect.width, (event.clientY - rect.top) * 1086 / rect.height);
      };
      stage.addEventListener("pointermove", (event) => {
        if (event.pointerType === "touch") return;
        const next = locate(event);
        if (next === active) return;
        clearHover();
        active = next;
        if (active) {
          active.button.classList.add("is-hovered");
          stage.classList.add("is-pointing");
        }
      }, { signal });
      stage.addEventListener("pointerleave", clearHover, { signal });
      stage.addEventListener("pointerdown", (event) => { pointerStart = { x: event.clientX, y: event.clientY }; }, { signal });
      stage.addEventListener("click", (event) => {
        // 键盘激活建筑按钮由通用弹窗管理器处理。
        if (event.target.closest("[data-season-building]")) return;
        if (pointerStart && Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) > 12) return;
        const record = locate(event);
        if (record) window.GuildModal?.open(record.button.getAttribute("data-guild-modal-open"), record.button);
      }, { signal });
    });

    forms.forEach((form) => {
      const modeSelect = form.querySelector("[data-season-mode]");
      const updateModeFields = () => {
        form.querySelectorAll("[data-season-mode-fields]").forEach(field => {
          field.hidden = field.dataset.seasonModeFields !== modeSelect?.value;
          field.querySelectorAll('input,select,textarea,button').forEach(control => {
            if (!control.hasAttribute('data-season-initial-disabled')) control.dataset.seasonInitialDisabled = String(control.disabled);
            control.disabled = field.hidden || control.dataset.seasonInitialDisabled === 'true';
          });
        });
        const travelLabel = modeSelect?.selectedOptions[0]?.dataset.seasonTravelLabel;
        const note = form.querySelector('[data-season-travel-note]');
        if (note && travelLabel) note.textContent = `基础单程 ${travelLabel}`;
      };
      modeSelect?.addEventListener("change", updateModeFields, { signal });
      updateModeFields();
      const configuredMaximum = Number(form.dataset.seasonSelectCount || 0);
      const coalitionLimit = Number(form.dataset.seasonCoalitionLimit || 0);
      const mode = form.querySelector("[data-season-mode]");
      const maximum = () => coalitionLimit && ["attack", "siege", "garrison", "escort"].includes(mode?.value)
        ? Math.min(configuredMaximum, coalitionLimit) : configuredMaximum;
      const minimum = Number(form.dataset.seasonSelectMin || 0);
      const selectionName = form.dataset.seasonSelectName;
      const choices = selectionName ? Array.from(form.querySelectorAll(`input[name="${selectionName}"]`)) : [];
      const feedback = form.querySelector("[data-season-feedback]");
      const selectedCount = () => choices.filter((choice) => choice.checked).length;
      const troopInputs = Array.from(form.querySelectorAll("[data-season-troops]"));
      const troopSummary = form.querySelector("[data-season-troop-summary]");
      const frontlinePreview = form.querySelector("[data-season-frontline-preview]");
      const foodPreview = form.querySelector("[data-season-food-preview]");
      const stationHours = form.querySelector("[data-season-station-hours]");
      const updateTroops = () => {
        const capacity = choices.filter((choice) => choice.checked).reduce((sum, choice) => sum + Number(choice.dataset.seasonCapacity || 0), 0);
        const troops = troopInputs.reduce((sum, input) => sum + Math.max(0, Number(input.value) || 0), 0);
        if (troopSummary) {
          troopSummary.textContent = `护院 ${troops} 人 / 带兵上限 ${capacity} 人`;
          troopSummary.classList.toggle("season-field-error", troops > capacity);
        }
        if (frontlinePreview) {
          const cap = Math.max(0, Number(frontlinePreview.dataset.seasonFrontlineCap) || 0);
          const others = Math.max(0, Number(frontlinePreview.dataset.seasonFrontlineOtherTroops) || 0);
          const total = troops + others;
          const estimated = total <= cap ? troops : Math.min(troops, Math.floor(cap * troops / total));
          frontlinePreview.textContent = `预计本波约 ${estimated} 人，其他为后备；按携带人数占用额度、消耗行军粮。集结人数变化后重算，实际以战斗为准。`;
        }
        const countLabel = form.querySelector("[data-season-troop-count]");
        if (countLabel) countLabel.textContent = mode?.value === "siege" && !troops ? "攻城必带" : `已选${troops}人`;
        if (foodPreview) {
          const weighted = troopInputs.reduce((sum, input) => sum + Math.max(0, Number(input.value) || 0)
            * (input.name === "troop_bloodthirsty_cavalry" ? Number(foodPreview.dataset.seasonCavalryRate) : 1), 0);
          const food = Math.ceil(weighted * Number(foodPreview.dataset.seasonFoodRate) * Number(stationHours?.value || 1) / 100);
          const carried = Math.ceil(weighted * Number(foodPreview.dataset.seasonFoodRate) * Math.min(Number(stationHours?.value || 1), Number(foodPreview.dataset.seasonCarriedHours || 1)) / 100);
          foodPreview.textContent = `预带 ${carried} 粮 · 驻军预计消耗 ${food} 粮，后续由城内补给`;
        }
        return troops <= capacity && (mode?.value !== "siege" || troops > 0);
      };
      const updateCount = (message) => {
        if (!feedback) return;
        feedback.textContent = message || `已选 ${selectedCount()} / ${maximum()} 名`;
        feedback.classList.toggle("is-error", Boolean(message));
      };
      choices.forEach((choice) => choice.addEventListener("change", () => {
        if (selectedCount() > maximum()) {
          choice.checked = false;
          updateCount(`最多选择 ${maximum()} 名。`);
        } else updateCount("");
        updateTroops();
      }, { signal }));
      if (choices.length) updateCount("");
      const updateModeLimit = () => {
        const selected = choices.filter((choice) => choice.checked);
        selected.slice(maximum()).forEach((choice) => { choice.checked = false; });
        const label = form.querySelector("[data-season-guest-limit]");
        if (label) label.textContent = `最多 ${maximum()} 名`;
        if (choices.length) updateCount(selected.length > maximum() ? `该行动最多选择 ${maximum()} 名。` : "");
        updateTroops();
      };
      mode?.addEventListener("change", updateModeLimit, { signal });
      if (mode) updateModeLimit();
      troopInputs.forEach((input) => input.addEventListener("input", updateTroops, { signal }));
      stationHours?.addEventListener("change", updateTroops, { signal });
      if (troopSummary) updateTroops();
      form.querySelector("[data-season-candidate-search]")?.addEventListener("input", (event) => {
        const term = event.target.value.trim().toLocaleLowerCase();
        form.querySelectorAll("[data-season-candidate]").forEach((label) => {
          // 已勾选的候选保留可见，避免筛选后忘记已选择的人物。
          label.hidden = !label.querySelector("input").checked && !label.dataset.seasonCandidate.toLocaleLowerCase().includes(term);
        });
      }, { signal });

      form.addEventListener("submit", (event) => {
        if (form.dataset.submitting === "1") { event.preventDefault(); return; }
        if (maximum()) {
          const error = selectionError(selectedCount(), minimum, maximum());
          if (error) { event.preventDefault(); updateCount(error); feedback?.scrollIntoView({ block: "nearest" }); return; }
        }
        if (troopSummary && !updateTroops()) {
          event.preventDefault();
          const config = form.querySelector(".season-troop-config");
          if (config) config.open = true;
          if (mode?.value === "siege" && !troopInputs.some(input => Number(input.value) > 0)) {
            troopSummary.textContent = "攻城需要至少携带1名阵营护院，请先配置。";
            troopSummary.classList.add("season-field-error");
          }
          troopSummary.scrollIntoView({ block: "nearest" });
          return;
        }
        form.dataset.submitting = "1";
        form.querySelectorAll('button[type="submit"]').forEach((button) => { button.disabled = true; button.textContent = "正在处理…"; });
      }, { signal });
    });

    const cleanup = () => {
      closeFactionTip();
      factionTip.remove();
      controller.abort();
      if (countdownTimer !== null) window.clearInterval(countdownTimer);
      recordSets.forEach((records) => records.forEach((record) => { record.mask = null; }));
      overlays.forEach((overlay) => {
        window.GuildModal?.close(overlay);
        overlay.remove();
      });
      if (window.seasonPageCleanup === cleanup) delete window.seasonPageCleanup;
    };
    window.seasonPageCleanup = cleanup;
    document.addEventListener("partial-nav:started", cleanup, { once: true, signal });
  }

  initializeSeasonPage();
  // 外部脚本仅加载一次，返回赛季页时重新绑定新页面节点。
  document.addEventListener("partial-nav:loaded", initializeSeasonPage);
})();
