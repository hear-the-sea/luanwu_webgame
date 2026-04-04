(() => {
  const warehousePageCore = window.WarehousePageCore;
  const WAREHOUSE_MODAL_CONFIG = {
    rebirth: {
      title: "选择要重生的门客",
      hint: "重生后门客将回到1级，属性将重置为模板基础值（有随机浮动）。装备将自动归还仓库，技能将被清空。",
      confirmText: "确认重生",
      listId: "guest-list-rebirth",
    },
    soul_fusion: {
      title: "选择要融合的门客",
      hint: "融合后门客会永久消失，已穿戴装备将自动归还仓库。生成饰品的属性会参考门客自身属性结构，并保留随机波动。",
      confirmText: "确认融合",
      listId: "guest-list-soul-fusion",
    },
    xisuidan: {
      title: "选择要洗髓的门客",
      hint: "洗髓丹可重新随机门客升级获得的成长点数。新的成长点数保证不低于当前值，有机会获得更高的属性成长。",
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
      hint: "升阶后门客将重置为1级，自动卸下装备，技能将被清空，并重置洗髓丹等计数；具体升阶目标由当前使用的残卷决定。",
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
    selectedGuestId: null,
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

  const saveScrollPosition = () => {
    const scrollTop = window.scrollY || document.documentElement.scrollTop;
    const tableWrapper = document.querySelector(".tw-table-wrapper");
    const tableScrollTop = tableWrapper ? tableWrapper.scrollTop : 0;
    sessionStorage.setItem("warehouseScrollTop", String(scrollTop));
    sessionStorage.setItem("warehouseTableScrollTop", String(tableScrollTop));
  };

  const restoreScrollPosition = () => {
    const savedScrollTop = sessionStorage.getItem("warehouseScrollTop");
    const savedTableScrollTop = sessionStorage.getItem("warehouseTableScrollTop");
    if (savedScrollTop !== null) {
      window.scrollTo(0, Number.parseInt(savedScrollTop, 10) || 0);
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

  const closeGuestSelectModal = () => {
    const modal = document.getElementById("guest-select-modal");
    if (modal) {
      modal.style.display = "none";
    }
    warehouseModalState.currentItemId = null;
    warehouseModalState.currentActionType = null;
    warehouseModalState.currentActionUrl = null;
    warehouseModalState.selectedGuestId = null;
    resetSoulFusionGuestFilter();
  };

  const openGuestSelectModal = (itemId, actionType, triggerElement = null) => {
    const config = WAREHOUSE_MODAL_CONFIG[actionType];
    if (!config) {
      return;
    }

    warehouseModalState.currentItemId = itemId;
    warehouseModalState.currentActionType = actionType;
    warehouseModalState.currentActionUrl = triggerElement?.dataset?.actionUrl || null;
    warehouseModalState.selectedGuestId = null;

    const modalTitle = document.getElementById("modal-title");
    const modalHint = document.getElementById("modal-hint");
    const confirmBtn = document.getElementById("modal-confirm-btn");
    if (!modalTitle || !modalHint || !confirmBtn) {
      return;
    }

    modalTitle.textContent = config.title;
    const requirementHint = actionType === "soul_fusion" ? formatSoulFusionRequirementHint(triggerElement) : "";
    modalHint.textContent = requirementHint ? `${config.hint} ${requirementHint}。` : config.hint;
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
    document.querySelectorAll(".tw-guest-option").forEach((option) => option.classList.remove("selected"));

    const modal = document.getElementById("guest-select-modal");
    if (modal) {
      modal.style.display = "flex";
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

    confirmBtn.disabled = true;
    confirmBtn.textContent = "处理中...";

    const formData = new FormData();
    formData.append("guest_id", warehouseModalState.selectedGuestId);
    formData.append("csrfmiddlewaretoken", document.querySelector("input[name='csrfmiddlewaretoken']")?.value || "");

    try {
      const response = await fetch(warehouseModalState.currentActionUrl, {
        method: "POST",
        body: formData,
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      const data = await response.json();
      if (data.success) {
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
    root.querySelectorAll(".tw-warehouse-actions form, .tw-action-return-form").forEach((form) => {
      if (form.dataset.ajaxBound === "1") {
        return;
      }
      form.dataset.ajaxBound = "1";
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const submitBtn = form.querySelector('button[type="submit"]');
        const originalText = submitBtn?.textContent || "";

        if (submitBtn) {
          submitBtn.disabled = true;
          submitBtn.textContent = "处理中...";
        }

        try {
          const response = await fetch(form.action, {
            method: "POST",
            body: new FormData(form),
            headers: { "X-Requested-With": "XMLHttpRequest" },
          });
          const data = await response.json();
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
    modal.addEventListener("click", (event) => {
      if (event.target === modal) {
        closeGuestSelectModal();
        return;
      }

      const guestOption = event.target.closest(".tw-guest-option");
      if (!guestOption || !warehouseModalState.currentActionType) {
        return;
      }

      const activeList = document.getElementById(WAREHOUSE_MODAL_CONFIG[warehouseModalState.currentActionType]?.listId);
      if (!activeList || !activeList.contains(guestOption)) {
        return;
      }

      activeList.querySelectorAll(".tw-guest-option").forEach((option) => option.classList.remove("selected"));
      guestOption.classList.add("selected");
      warehouseModalState.selectedGuestId = guestOption.dataset.guestId || null;

      const confirmBtn = document.getElementById("modal-confirm-btn");
      if (confirmBtn) {
        confirmBtn.disabled = false;
      }
    });
  };

  const initWarehousePage = () => {
    if (!document.querySelector(".tw-warehouse-card")) {
      return;
    }

    bindWarehouseActionForms();
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
