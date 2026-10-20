(() => {
  const warehousePageCore = window.WarehousePageCore;
  const WAREHOUSE_MODAL_CONFIG = {
    rebirth: {
      title: "选择要重生的门客",
      hint: "重生后门客将回到1级，属性将重置为模板基础值（有随机浮动）。装备将自动归还仓库，技能将被清空。",
      confirmText: "确认重生",
      listId: "guest-list-rebirth",
    },
    disguise: {
      title: "选择要易容的门客",
      hint: "使用后可将所选门客的显示形象替换为吕布，并使其生命+800、武力+20、敏捷+20。效果不会重复叠加。",
      confirmText: "确认易容",
      listId: "guest-list-disguise",
    },
    avatar_disguise: {
      title: "选择门客与新形象",
      hint: "选择任意门客，将其易容为选择的形象。",
      confirmText: "确认易容",
      listId: "guest-list-avatar-disguise",
      requiresImage: true,
    },
    soul_fusion: {
      title: "选择要融合的门客",
      hint: "融合后门客会永久消失，已穿戴装备将自动归还仓库。生成饰品的属性会参考门客自身属性结构，并保留随机波动。",
      confirmText: "确认融合",
      listId: "guest-list-soul-fusion",
    },
    xisuidan: {
      title: "选择要洗髓的门客",
      hint: "洗髓丹能提升门客的资质，但成长方向可能发生某些未知改变哦~仅满级门客能够使用",
      confirmText: "确认洗髓",
      listId: "guest-list-xisuidan",
    },
    xidianka: {
      title: "选择要洗点的门客",
      hint: "洗点卡可重置门客的属性点分配。已分配的属性点将全部返还，可重新分配。",
      confirmText: "确认洗点",
      listId: "guest-list-xidianka",
    },
    rarity_upgrade: {
      title: "选择要升阶的门客",
      hint: "升阶后门客将重置为1级，自动卸下装备，技能将被清空，并重置洗髓丹等计数；具体目标品质由当前道具决定。",
      confirmText: "确认升阶",
      listId: "guest-list-rarity-upgrade",
    },
  };

  const WAREHOUSE_GUEST_LIST_IDS = Object.values(WAREHOUSE_MODAL_CONFIG).map((config) => config.listId);
  const SOUL_FUSION_RARITY_LABELS = {
    green: "绿色",
    blue: "蓝色",
    purple: "紫色",
  };

  const warehouseModalState = {
    currentActionType: null,
    currentActionUrl: null,
    currentItemId: null,
    currentBindingMode: null,
    selectedGuestId: null,
    selectedImageKey: null,
    lastTriggerElement: null,
  };
  const targetManorModalState = {
    lastTriggerElement: null,
    searchGeneration: 0,
  };

  const showErrorDialog = (message, title = "错误") => {
    if (window.gameDialog?.error) {
      return window.gameDialog.error(message, { title });
    }
    window.alert(message);
    return Promise.resolve();
  };

  const showSuccessDialog = (message) => {
    if (window.gameDialog?.success) {
      return window.gameDialog.success(message);
    }
    window.alert(message);
    return Promise.resolve();
  };

  const confirmWarehouseUseForm = async (form) => {
    if (form.dataset.targetModal === "true") {
      return true;
    }
    const statusSummary = form.dataset.worldUniqueStatusSummary;
    const guestName = form.dataset.worldUniqueGuestName || "该门客";
    const configuredConfirmText = form.dataset.confirmText;
    const confirmText = statusSummary
      ? `${guestName}当前状态：${statusSummary}\n\n${configuredConfirmText || `确认使用${guestName}召唤卷轴？`}`
      : configuredConfirmText;
    if (!confirmText) {
      return true;
    }

    if (window.gameConfirm) {
      return window.gameConfirm(confirmText, {
        title: form.dataset.confirmTitle || "使用确认",
        okText: form.dataset.confirmOkText || "确认使用",
      });
    }
    if (window.gameDialog?.confirm) {
      return window.gameDialog.confirm(confirmText, {
        title: form.dataset.confirmTitle || "使用确认",
        okText: form.dataset.confirmOkText || "确认使用",
      });
    }
    return window.confirm(confirmText);
  };

  const confirmResourceOverflow = async (data) => {
    const message = data.confirmation_message || data.error || "部分资源将因容量上限无法获得，是否仍要使用？";
    const options = {
      title: data.confirmation_title || "资源溢出确认",
      okText: data.confirmation_ok_text || "仍然使用",
      cancelText: "先不使用",
      danger: true,
    };
    if (window.gameConfirm) {
      return window.gameConfirm(message, options);
    }
    if (window.gameDialog?.confirm) {
      return window.gameDialog.confirm(message, options);
    }
    return window.confirm(message);
  };

  const saveScrollPosition = () => {
    const contentArea = document.querySelector(".content-area");
    const scrollTop = window.scrollY || document.documentElement.scrollTop;
    const tableWrapper = document.querySelector(".tw-table-wrapper");
    const tableScrollTop = tableWrapper ? tableWrapper.scrollTop : 0;
    sessionStorage.setItem("warehouseContentScrollTop", String(contentArea ? contentArea.scrollTop : scrollTop));
    sessionStorage.setItem("warehouseScrollTop", String(scrollTop));
    sessionStorage.setItem("warehouseTableScrollTop", String(tableScrollTop));
  };

  const restoreScrollPosition = () => {
    const savedContentScrollTop = sessionStorage.getItem("warehouseContentScrollTop");
    const savedScrollTop = sessionStorage.getItem("warehouseScrollTop");
    const savedTableScrollTop = sessionStorage.getItem("warehouseTableScrollTop");
    const contentArea = document.querySelector(".content-area");
    if (savedContentScrollTop !== null && contentArea) {
      contentArea.scrollTop = Number.parseInt(savedContentScrollTop, 10) || 0;
    } else if (savedScrollTop !== null) {
      window.scrollTo(0, Number.parseInt(savedScrollTop, 10) || 0);
    }
    if (savedContentScrollTop !== null) {
      sessionStorage.removeItem("warehouseContentScrollTop");
    }
    if (savedScrollTop !== null) {
      sessionStorage.removeItem("warehouseScrollTop");
    }
    if (savedTableScrollTop !== null) {
      const tableWrapper = document.querySelector(".tw-table-wrapper");
      if (tableWrapper) {
        tableWrapper.scrollTop = Number.parseInt(savedTableScrollTop, 10) || 0;
      }
      sessionStorage.removeItem("warehouseTableScrollTop");
    }
  };

  const formatSoulFusionRequirementHint = (triggerElement) => {
    if (!triggerElement) {
      return "";
    }
    return warehousePageCore.formatSoulFusionRequirementHint(
      warehousePageCore.buildWarehouseFilterState(triggerElement.dataset),
      SOUL_FUSION_RARITY_LABELS
    );
  };

  const resetSoulFusionGuestFilter = () => {
    const list = document.getElementById("guest-list-soul-fusion");
    if (!list) {
      return;
    }
    list.querySelectorAll(".tw-guest-option").forEach((option) => {
      option.style.display = "";
      option.classList.remove("selected");
    });
    const emptyHint = document.getElementById("guest-list-soul-fusion-empty");
    if (emptyHint) {
      emptyHint.style.display = "none";
    }
  };

  const applySoulFusionGuestFilter = (triggerElement) => {
    const list = document.getElementById("guest-list-soul-fusion");
    if (!list) {
      return;
    }

    const filterState = warehousePageCore.buildWarehouseFilterState(triggerElement?.dataset);
    let visibleCount = 0;

    list.querySelectorAll(".tw-guest-option").forEach((option) => {
      const isVisible = warehousePageCore.shouldGuestMatchWarehouseFilter(
        {
          level: option.dataset.guestLevel,
          rarity: option.dataset.guestRarity,
        },
        filterState
      );
      option.style.display = isVisible ? "" : "none";
      option.classList.remove("selected");
      if (isVisible) {
        visibleCount += 1;
      }
    });

    const emptyHint = document.getElementById("guest-list-soul-fusion-empty");
    if (emptyHint) {
      emptyHint.style.display = visibleCount === 0 ? "block" : "none";
    }
  };

  const applyRarityUpgradeGuestFilter = (triggerElement = null) => {
    const list = document.getElementById("guest-list-rarity-upgrade");
    if (!list) {
      return;
    }

    const sourceRarity = triggerElement?.dataset?.rarityUpgradeSourceRarity || "";
    let availableCount = 0;
    list.querySelectorAll(".tw-guest-option").forEach((option) => {
      const isVisible = !sourceRarity || option.dataset.guestRarity === sourceRarity;
      const blockReason = sourceRarity ? option.dataset.rarityUpgradeBlockReason || "" : "";
      option.style.display = isVisible ? "" : "none";
      option.disabled = Boolean(blockReason);
      option.classList.remove("selected");
      const reasonText = option.querySelector("[data-rarity-upgrade-block-reason-text]");
      if (reasonText) {
        reasonText.style.display = blockReason ? "" : "none";
      }
      if (isVisible && !blockReason) {
        availableCount += 1;
      }
    });

    const emptyHint = document.getElementById("guest-list-rarity-upgrade-empty");
    if (emptyHint) {
      emptyHint.style.display = availableCount === 0 ? "block" : "none";
    }
  };

  const closeGuestSelectModal = () => {
    const modal = document.getElementById("guest-select-modal");
    if (modal) {
      modal.style.display = "none";
      modal.setAttribute("aria-hidden", "true");
    }
    warehouseModalState.currentItemId = null;
    warehouseModalState.currentBindingMode = null;
    warehouseModalState.currentActionType = null;
    warehouseModalState.currentActionUrl = null;
    warehouseModalState.selectedGuestId = null;
    warehouseModalState.selectedImageKey = null;
    const lastTriggerElement = warehouseModalState.lastTriggerElement;
    warehouseModalState.lastTriggerElement = null;
    if (lastTriggerElement && lastTriggerElement.isConnected && typeof lastTriggerElement.focus === "function") {
      lastTriggerElement.focus();
    }
    resetSoulFusionGuestFilter();
  };

  const openGuestSelectModal = (itemId, actionType, triggerElement = null) => {
    const config = WAREHOUSE_MODAL_CONFIG[actionType];
    if (!config) {
      return;
    }

    warehouseModalState.currentItemId = itemId;
    warehouseModalState.currentBindingMode = triggerElement?.dataset?.bindingMode || null;
    warehouseModalState.currentActionType = actionType;
    warehouseModalState.currentActionUrl = triggerElement?.dataset?.actionUrl || null;
    warehouseModalState.selectedGuestId = null;
    warehouseModalState.selectedImageKey = null;
    warehouseModalState.lastTriggerElement = triggerElement;

    const modalTitle = document.getElementById("modal-title");
    const modalHint = document.getElementById("modal-hint");
    const confirmBtn = document.getElementById("modal-confirm-btn");
    if (!modalTitle || !modalHint || !confirmBtn) {
      return;
    }

    modalTitle.textContent = config.title;
    const requirementHint = actionType === "soul_fusion" ? formatSoulFusionRequirementHint(triggerElement) : "";
    modalHint.textContent = requirementHint ? `${config.hint} ${requirementHint}。` : config.hint;
    if (actionType === "rarity_upgrade" && triggerElement?.dataset?.rarityUpgradeSourceRarity) {
      modalHint.textContent += " 仅可招募的门客能够使用丸子，每名门客只能使用一次绿转蓝或蓝转紫丸子。";
    }
    confirmBtn.textContent = config.confirmText;
    confirmBtn.disabled = true;

    resetSoulFusionGuestFilter();
    WAREHOUSE_GUEST_LIST_IDS.forEach((id) => {
      const list = document.getElementById(id);
      if (list) {
        list.style.display = "none";
      }
    });

    const activeList = document.getElementById(config.listId);
    if (activeList) {
      activeList.style.display = "block";
    }
    if (actionType === "soul_fusion") {
      applySoulFusionGuestFilter(triggerElement);
    }
    if (actionType === "rarity_upgrade") {
      applyRarityUpgradeGuestFilter(triggerElement);
    }
    document.querySelectorAll(".tw-guest-option").forEach((option) => option.classList.remove("selected"));
    document.querySelectorAll(".tw-avatar-option").forEach((option) => option.classList.remove("selected"));

    const modal = document.getElementById("guest-select-modal");
    if (modal) {
      modal.style.display = "flex";
      modal.setAttribute("aria-hidden", "false");
      const visibleOption = activeList
        ? [...activeList.querySelectorAll(".tw-guest-option")].find((option) => !option.disabled && option.getClientRects().length > 0)
        : null;
      if (visibleOption) {
        visibleOption.focus();
      } else {
        modal.focus();
      }
    }
  };

  const getVisibleModalFocusables = () => {
    const modal = document.getElementById("guest-select-modal");
    if (!modal) {
      return [];
    }
    return [...modal.querySelectorAll(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )].filter((element) => element.getClientRects().length > 0);
  };

  const handleGuestModalKeydown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeGuestSelectModal();
      return;
    }
    if (event.key !== "Tab") {
      return;
    }

    const focusable = getVisibleModalFocusables();
    if (focusable.length === 0) {
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const confirmGuestAction = async () => {
    if (
      !warehouseModalState.selectedGuestId
      || !warehouseModalState.currentItemId
      || !warehouseModalState.currentActionType
      || !warehouseModalState.currentActionUrl
    ) {
      await showErrorDialog("请先选择门客", "提示");
      return;
    }

    const config = WAREHOUSE_MODAL_CONFIG[warehouseModalState.currentActionType];
    const confirmBtn = document.getElementById("modal-confirm-btn");
    if (!config || !confirmBtn) {
      return;
    }
    if (config.requiresImage && !warehouseModalState.selectedImageKey) {
      await showErrorDialog("请选择新的门客形象", "提示");
      return;
    }

    confirmBtn.disabled = true;
    confirmBtn.textContent = "处理中...";

    const formData = new FormData();
    formData.append("guest_id", warehouseModalState.selectedGuestId);
    if (warehouseModalState.currentBindingMode) {
      formData.append("binding_mode", warehouseModalState.currentBindingMode);
    }
    if (config.requiresImage) {
      formData.append("avatar_key", warehouseModalState.selectedImageKey);
    }
    formData.append("csrfmiddlewaretoken", document.querySelector("input[name='csrfmiddlewaretoken']")?.value || "");

    try {
      const response = await fetch(warehouseModalState.currentActionUrl, {
        method: "POST",
        body: formData,
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      const data = await response.json();
      if (data.success) {
        saveScrollPosition();
        closeGuestSelectModal();
        await showSuccessDialog(data.message || "操作成功");
        window.location.reload();
        return;
      }

      await showErrorDialog(data.error || "操作失败");
      confirmBtn.disabled = false;
      confirmBtn.textContent = config.confirmText;
    } catch (_error) {
      await showErrorDialog("请求失败，请重试");
      confirmBtn.disabled = false;
      confirmBtn.textContent = config.confirmText;
    }
  };

  const bindWarehouseActionForms = (root = document) => {
    root.querySelectorAll(".tw-warehouse-actions form, .tw-action-return-form, #target-item-use-form, .js-player-choice-use-form").forEach((form) => {
      if (form.dataset.ajaxBound === "1") {
        return;
      }
      form.dataset.ajaxBound = "1";
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const submitBtn = form.querySelector('button[type="submit"]');
        const originalText = submitBtn?.textContent || "";

        const confirmed = await confirmWarehouseUseForm(form);
        if (!confirmed) {
          return;
        }

        if (submitBtn) {
          submitBtn.disabled = true;
          submitBtn.textContent = "处理中...";
        }

        try {
          const formData = new FormData(form);
          let response = await fetch(form.action, {
            method: "POST",
            body: formData,
            headers: { "X-Requested-With": "XMLHttpRequest" },
          });
          let data = await response.json();

          while (data.requires_confirmation && data.confirmation_type === "resource_overflow") {
            const confirmationToken = data.confirmation_token;
            if (typeof confirmationToken !== "string" || !confirmationToken) {
              await showErrorDialog("资源确认信息已失效，请重新操作");
              if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.textContent = originalText;
              }
              return;
            }

            const overflowConfirmed = await confirmResourceOverflow(data);
            if (!overflowConfirmed) {
              if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.textContent = originalText;
              }
              return;
            }

            formData.set("resource_overflow_confirmation", confirmationToken);
            response = await fetch(form.action, {
              method: "POST",
              body: formData,
              headers: { "X-Requested-With": "XMLHttpRequest" },
            });
            data = await response.json();
          }

          if (data.success) {
            saveScrollPosition();
            if (data.message) {
              await showSuccessDialog(data.message);
            }
            window.location.reload();
            return;
          }

          await showErrorDialog(data.error || "操作失败");
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = originalText;
          }
        } catch (_error) {
          saveScrollPosition();
          window.location.reload();
        }
      });
    });
  };

  const bindTargetManorSearch = (root = document) => {
    root.querySelectorAll(".js-target-item-form").forEach((form) => {
      if (form.dataset.targetSearchBound === "1") {
        return;
      }
      form.dataset.targetSearchBound = "1";
      const searchInput = form.querySelector(".tw-target-manor-search");
      const searchButton = form.querySelector(".js-search-target-manors");
      const results = form.querySelector(".tw-target-manor-results");
      const status = form.querySelector("[data-target-manor-status]");
      const confirmButton = form.querySelector("#target-manor-confirm-btn");
      if (!searchInput || !searchButton || !results) {
        return;
      }

      searchInput.addEventListener("input", () => {
        targetManorModalState.searchGeneration += 1;
        results.replaceChildren();
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "请重新搜索并选择庄园";
        results.appendChild(placeholder);
        if (confirmButton) confirmButton.disabled = true;
        searchButton.disabled = false;
        searchButton.textContent = "搜索";
        if (status) status.textContent = "";
      });
      results.addEventListener("change", () => {
        if (confirmButton) confirmButton.disabled = !results.value;
      });
      searchInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          searchButton.click();
        }
      });
      searchButton.addEventListener("click", async () => {
        const query = searchInput.value.trim();
        if (query.length < 2) {
          await showErrorDialog("请输入至少两个字的庄园名称", "查找目标庄园");
          return;
        }

        const originalText = searchButton.textContent;
        const searchGeneration = ++targetManorModalState.searchGeneration;
        searchButton.disabled = true;
        searchButton.textContent = "查找中…";
        if (status) status.textContent = "正在查找庄园…";
        try {
          const url = new URL(form.dataset.targetSearchUrl, window.location.origin);
          url.searchParams.set("type", "name");
          url.searchParams.set("q", query);
          const response = await fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } });
          const data = await response.json();
          if (!response.ok || !data.success) {
            throw new Error(data.error || "目标庄园搜索失败");
          }
          if (
            searchGeneration !== targetManorModalState.searchGeneration ||
            searchInput.value.trim() !== query
          ) {
            return;
          }

          results.replaceChildren();
          const placeholder = document.createElement("option");
          placeholder.value = "";
          placeholder.textContent = data.results?.length ? "请选择目标庄园" : "没有找到庄园";
          results.appendChild(placeholder);
          const manors = Array.isArray(data.results) ? data.results : [];
          manors.forEach((manor) => {
            const option = document.createElement("option");
            option.value = String(manor.id);
            const location = manor.location_display || manor.region_display || "未知地区";
            const protection = manor.can_attack ? "" : ` · ${manor.attack_reason || "当前受保护"}`;
            option.textContent = `${manor.name}（${location} · 声望${manor.prestige}${protection}）`;
            option.disabled = manor.can_attack === false;
            results.appendChild(option);
          });
          results.value = "";
          if (confirmButton) confirmButton.disabled = true;
          if (status) {
            const attackableCount = manors.filter((manor) => manor.can_attack === true).length;
            status.textContent = !manors.length
              ? "没有找到匹配的庄园。"
              : attackableCount
                ? `找到 ${manors.length} 个庄园，其中 ${attackableCount} 个可攻击。`
                : `找到 ${manors.length} 个庄园，当前都受攻击保护。`;
          }
        } catch (error) {
          if (searchGeneration !== targetManorModalState.searchGeneration) {
            return;
          }
          if (status) status.textContent = "庄园搜索失败，请稍后重试。";
          await showErrorDialog(error instanceof Error ? error.message : "目标庄园搜索失败", "查找目标庄园");
        } finally {
          if (searchGeneration === targetManorModalState.searchGeneration) {
            searchButton.disabled = false;
            searchButton.textContent = originalText;
          }
        }
      });
    });
  };

  const closeTargetManorModal = () => {
    targetManorModalState.searchGeneration += 1;
    const modal = document.getElementById("target-manor-modal");
    const form = document.getElementById("target-item-use-form");
    if (modal) {
      modal.style.display = "none";
      modal.setAttribute("aria-hidden", "true");
    }
    if (form) {
      form.action = "#";
      form.dataset.itemName = "";
      const searchInput = form.querySelector(".tw-target-manor-search");
      const results = form.querySelector(".tw-target-manor-results");
      const status = form.querySelector("[data-target-manor-status]");
      const searchButton = form.querySelector(".js-search-target-manors");
      const confirmButton = form.querySelector("#target-manor-confirm-btn");
      const bindingMode = form.querySelector('input[name="binding_mode"]');
      if (searchInput) searchInput.value = "";
      if (results) {
        results.replaceChildren();
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "先搜索并选择庄园";
        results.appendChild(placeholder);
      }
      if (status) status.textContent = "";
      if (searchButton) {
        searchButton.disabled = false;
        searchButton.textContent = "搜索";
      }
      if (confirmButton) {
        confirmButton.disabled = true;
        confirmButton.textContent = "确认使用";
      }
      if (bindingMode) bindingMode.value = "";
    }
    const lastTriggerElement = targetManorModalState.lastTriggerElement;
    targetManorModalState.lastTriggerElement = null;
    if (lastTriggerElement && lastTriggerElement.isConnected && typeof lastTriggerElement.focus === "function") {
      lastTriggerElement.focus();
    }
  };

  const openTargetManorModal = (triggerElement) => {
    const modal = document.getElementById("target-manor-modal");
    const form = document.getElementById("target-item-use-form");
    const title = document.getElementById("target-manor-modal-title");
    const hint = document.getElementById("target-manor-modal-hint");
    const searchInput = form?.querySelector(".tw-target-manor-search");
    const results = form?.querySelector(".tw-target-manor-results");
    const status = form?.querySelector("[data-target-manor-status]");
    const searchButton = form?.querySelector(".js-search-target-manors");
    const confirmButton = form?.querySelector("#target-manor-confirm-btn");
    const bindingMode = form?.querySelector('input[name="binding_mode"]');
    if (!modal || !form || !triggerElement?.dataset?.actionUrl) {
      return;
    }

    targetManorModalState.searchGeneration += 1;
    const itemName = triggerElement.dataset.itemName || "道具";
    form.action = triggerElement.dataset.actionUrl;
    form.dataset.itemName = itemName;
    if (searchInput) searchInput.value = "";
    if (results) {
      results.replaceChildren();
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "先搜索并选择庄园";
      results.appendChild(placeholder);
    }
    if (status) status.textContent = "";
    if (searchButton) {
      searchButton.disabled = false;
      searchButton.textContent = "搜索";
    }
    if (confirmButton) {
      confirmButton.disabled = true;
      confirmButton.textContent = "确认使用";
    }
    if (bindingMode) bindingMode.value = triggerElement.dataset.bindingMode || "";
    if (title) title.textContent = `选择目标庄园 · ${itemName}`;
    if (hint) {
      hint.textContent = `选择要使用「${itemName}」的庄园。目标需符合攻击保护规则，同类道具对同一庄园有1小时冷却。`;
    }
    targetManorModalState.lastTriggerElement = triggerElement;
    modal.style.display = "flex";
    modal.setAttribute("aria-hidden", "false");
    searchInput?.focus();
  };

  const handleTargetManorModalKeydown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeTargetManorModal();
      return;
    }
    if (event.key !== "Tab") {
      return;
    }
    const modal = document.getElementById("target-manor-modal");
    const focusables = modal
      ? [...modal.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled])')]
        .filter((element) => element.getClientRects().length > 0)
      : [];
    if (focusables.length === 0) {
      return;
    }
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const bindTargetItemModalControls = (root = document) => {
    root.querySelectorAll(".js-open-target-item-modal").forEach((button) => {
      if (button.dataset.boundClick === "1") return;
      button.dataset.boundClick = "1";
      button.addEventListener("click", () => openTargetManorModal(button));
    });

    root.querySelectorAll(".js-close-target-manor-modal").forEach((button) => {
      if (button.dataset.boundClick === "1") return;
      button.dataset.boundClick = "1";
      button.addEventListener("click", closeTargetManorModal);
    });

    const modal = root.getElementById ? root.getElementById("target-manor-modal") : document.getElementById("target-manor-modal");
    if (!modal || modal.dataset.boundClick === "1") return;
    modal.dataset.boundClick = "1";
    modal.addEventListener("keydown", handleTargetManorModalKeydown);
    modal.addEventListener("click", (event) => {
      if (event.target === modal) closeTargetManorModal();
    });
  };

  const bindGuestModalControls = (root = document) => {
    root.querySelectorAll(".js-open-guest-select-modal").forEach((button) => {
      if (button.dataset.boundClick === "1") {
        return;
      }
      button.dataset.boundClick = "1";
      button.addEventListener("click", () => {
        openGuestSelectModal(button.dataset.itemId, button.dataset.actionType, button);
      });
    });

    root.querySelectorAll(".js-close-guest-select-modal").forEach((button) => {
      if (button.dataset.boundClick === "1") {
        return;
      }
      button.dataset.boundClick = "1";
      button.addEventListener("click", closeGuestSelectModal);
    });

    const confirmBtn = root.getElementById ? root.getElementById("modal-confirm-btn") : document.getElementById("modal-confirm-btn");
    if (confirmBtn && confirmBtn.dataset.boundClick !== "1") {
      confirmBtn.dataset.boundClick = "1";
      confirmBtn.addEventListener("click", () => {
        void confirmGuestAction();
      });
    }

    const categorySelect = root.getElementById ? root.getElementById("category") : document.getElementById("category");
    if (categorySelect && categorySelect.dataset.boundChange !== "1") {
      categorySelect.dataset.boundChange = "1";
      categorySelect.addEventListener("change", () => {
        categorySelect.form?.submit();
      });
    }
  };

  const bindGuestModalSelection = (root = document) => {
    const modal = root.getElementById ? root.getElementById("guest-select-modal") : document.getElementById("guest-select-modal");
    if (!modal || modal.dataset.boundClick === "1") {
      return;
    }
    modal.dataset.boundClick = "1";
    if (modal.dataset.boundKeyboard !== "1") {
      modal.dataset.boundKeyboard = "1";
      modal.addEventListener("keydown", handleGuestModalKeydown);
    }
    modal.addEventListener("click", (event) => {
      if (event.target === modal) {
        closeGuestSelectModal();
        return;
      }

      const guestOption = event.target.closest(".tw-guest-option");
      const avatarOption = event.target.closest(".tw-avatar-option");
      if (!warehouseModalState.currentActionType) {
        return;
      }

      const activeConfig = WAREHOUSE_MODAL_CONFIG[warehouseModalState.currentActionType];
      const activeList = document.getElementById(activeConfig?.listId);
      if (!activeList) {
        return;
      }

      if (avatarOption && activeConfig?.requiresImage && activeList.contains(avatarOption)) {
        activeList.querySelectorAll(".tw-avatar-option").forEach((option) => option.classList.remove("selected"));
        avatarOption.classList.add("selected");
        warehouseModalState.selectedImageKey = avatarOption.dataset.avatarKey || null;
        const confirmBtn = document.getElementById("modal-confirm-btn");
        if (confirmBtn) {
          confirmBtn.disabled = !warehouseModalState.selectedGuestId || !warehouseModalState.selectedImageKey;
        }
        return;
      }

      if (!guestOption || guestOption.disabled || !activeList.contains(guestOption)) {
        return;
      }

      activeList.querySelectorAll(".tw-guest-option").forEach((option) => option.classList.remove("selected"));
      guestOption.classList.add("selected");
      warehouseModalState.selectedGuestId = guestOption.dataset.guestId || null;

      const confirmBtn = document.getElementById("modal-confirm-btn");
      if (confirmBtn) {
        confirmBtn.disabled = activeConfig?.requiresImage
          ? !warehouseModalState.selectedGuestId || !warehouseModalState.selectedImageKey
          : false;
      }
    });
  };

  const initWarehousePage = () => {
    if (!document.querySelector(".tw-warehouse-card")) {
      return;
    }

    bindWarehouseActionForms();
    bindTargetItemModalControls();
    bindTargetManorSearch();
    bindGuestModalControls();
    bindGuestModalSelection();
    restoreScrollPosition();

    if (typeof window.initItemTooltip === "function") {
      window.initItemTooltip({ key: "warehouse", trackPointer: false });
    }
  };

  document.addEventListener("DOMContentLoaded", initWarehousePage);
  document.addEventListener("partial-nav:loaded", initWarehousePage);
})();
