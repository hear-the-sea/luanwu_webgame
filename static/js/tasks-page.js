(() => {
  const initTasksPage = () => {
    if (!document.querySelector(".tw-mission-tabs")) {
      return;
    }

    if (typeof window.initItemTooltip === "function") {
      window.initItemTooltip({
        key: "tasks_mission_drops",
        cellSelector: ".tw-drop-entry",
        tooltipSelector: ".tw-item-tooltip",
        trackPointer: false,
        enableTouch: true,
      });
    }

    const tabs = document.querySelectorAll(".tw-trade-tab");
    const contents = document.querySelectorAll(".mission-tab-content");
    tabs.forEach((tab) => {
      if (tab.dataset.clickBound === "1") {
        return;
      }
      tab.dataset.clickBound = "1";
      tab.addEventListener("click", () => {
        tabs.forEach((item) => item.classList.remove("active"));
        contents.forEach((content) => {
          content.style.display = "none";
          content.classList.remove("active");
        });
        tab.classList.add("active");
        const tabId = tab.dataset.tab;
        const content = document.getElementById(`tab-${tabId}`);
        if (content) {
          content.style.display = "block";
          window.setTimeout(() => content.classList.add("active"), 10);
        }
        if (window.history && window.history.replaceState && window.TasksPageCore?.buildTaskTabUrl) {
          window.history.replaceState({}, "", window.TasksPageCore.buildTaskTabUrl(window.location.href, tabId));
        }
      });
    });

    const loadoutForm = document.querySelector("[data-task-loadout]");
    if (loadoutForm && loadoutForm.dataset.loadoutInitialized !== "1") {
      loadoutForm.dataset.loadoutInitialized = "1";
      const guestInputs = Array.from(loadoutForm.querySelectorAll("[data-task-guest]"));
      const troopInputs = Array.from(loadoutForm.querySelectorAll("[data-task-troop]"));
      const maxSquadSize = Math.max(0, Number.parseInt(loadoutForm.dataset.maxSquadSize || "0", 10) || 0);
      const selectedCount = loadoutForm.querySelector("#selected-guest-count");
      const troopTotal = loadoutForm.querySelector("[data-task-troop-total]");
      const troopCapacity = loadoutForm.querySelector("[data-task-troop-capacity]");
      const capacityStatus = loadoutForm.querySelector("[data-task-capacity-status]");
      const submitButton = loadoutForm.querySelector("[data-task-submit]");
      const quickButton = loadoutForm.querySelector("[data-task-quick-launch]");
      const quickAllowed = Boolean(quickButton && !quickButton.disabled);
      const parseCount = (value) => Math.max(0, Number.parseInt(value, 10) || 0);
      const checkedGuests = () => guestInputs.filter((input) => input.checked);
      const currentCapacity = () => checkedGuests().reduce(
        (total, input) => total + parseCount(input.dataset.troopCapacity), 0
      );
      const currentTroopTotal = () => troopInputs.reduce(
        (total, input) => total + parseCount(input.value), 0
      );

      const updateLoadout = () => {
        const guestCount = checkedGuests().length;
        const capacity = currentCapacity();
        const troops = currentTroopTotal();
        if (selectedCount) selectedCount.textContent = String(guestCount);
        if (troopTotal) troopTotal.textContent = String(troops);
        if (troopCapacity) troopCapacity.textContent = String(capacity);
        if (capacityStatus) {
          const overCapacity = troops > capacity;
          capacityStatus.textContent = guestCount === 0
            ? troops > 0
              ? "已携带护院，请选择门客或全部清零"
              : "可先配置护院；选择门客后计算带兵上限"
            : overCapacity
              ? `已超出带兵上限 ${troops - capacity} 人`
              : `还可携带 ${capacity - troops} 人`;
          capacityStatus.classList.toggle("is-over-limit", overCapacity);
          capacityStatus.setAttribute("role", overCapacity ? "alert" : "status");
        }
        if (submitButton) {
          submitButton.disabled = guestCount === 0 || troops > capacity || loadoutForm.dataset.quickPending === "1";
        }
        if (quickButton) {
          quickButton.disabled = !quickAllowed || maxSquadSize === 0
            || !guestInputs.some((input) => !input.disabled) || loadoutForm.dataset.quickPending === "1";
        }
      };

      loadoutForm.addEventListener("task-loadout:restore", updateLoadout);

      const setTroopValue = (input, requested) => {
        const inventoryMax = parseCount(input.dataset.max);
        const next = Math.min(parseCount(requested), inventoryMax);
        input.value = String(next);
        updateLoadout();
      };

      guestInputs.forEach((input) => {
        input.addEventListener("change", () => {
          if (checkedGuests().length > maxSquadSize) {
            input.checked = false;
            if (typeof window.gameAlert === "function") {
              window.gameAlert(`最多只能选择 ${maxSquadSize} 名门客出征`, { title: "选择限制" });
            } else {
              window.alert(`最多只能选择 ${maxSquadSize} 名门客出征`);
            }
          }
          updateLoadout();
        });
      });

      loadoutForm.querySelector("[data-task-select-all]")?.addEventListener("click", () => {
        let remaining = maxSquadSize;
        guestInputs.forEach((input) => {
          if (!input.checked) return;
          if (remaining > 0) remaining -= 1;
          else input.checked = false;
        });
        guestInputs.forEach((input) => {
          if (!input.checked && remaining > 0) {
            input.checked = true;
            remaining -= 1;
          }
        });
        updateLoadout();
      });

      loadoutForm.querySelector("[data-task-clear-guests]")?.addEventListener("click", () => {
        guestInputs.forEach((input) => { input.checked = false; });
        updateLoadout();
      });

      troopInputs.forEach((input) => {
        input.addEventListener("input", updateLoadout);
        input.addEventListener("change", () => setTroopValue(input, input.value));
      });

      loadoutForm.querySelectorAll("[data-task-adjust]").forEach((button) => {
        button.addEventListener("click", () => {
          const input = troopInputs.find((item) => item.dataset.troopKey === button.dataset.troopKey);
          if (input) setTroopValue(input, parseCount(input.value) + Number.parseInt(button.dataset.taskAdjust, 10));
        });
      });

      loadoutForm.querySelectorAll("[data-task-fill]").forEach((button) => {
        button.addEventListener("click", () => {
          const input = troopInputs.find((item) => item.dataset.troopKey === button.dataset.taskFill);
          if (input) setTroopValue(input, input.dataset.max);
        });
      });

      loadoutForm.querySelector("[data-task-clear-troops]")?.addEventListener("click", () => {
        troopInputs.forEach((input) => {
          input.value = "0";
        });
        updateLoadout();
      });

      quickButton?.addEventListener("click", async () => {
        if (quickButton.disabled || loadoutForm.dataset.quickPending === "1") return;
        const plannedGuests = guestInputs.filter((input) => !input.disabled).slice(0, maxSquadSize);
        const plannedTroops = troopInputs.filter((input) => !input.disabled && parseCount(input.dataset.max) > 0);
        loadoutForm.dataset.quickPending = "1";
        updateLoadout();
        let submitted = false;
        try {
          const message = loadoutForm.dataset.taskGuestOnly === "1"
            ? `将自动选择 ${plannedGuests.length} 名门客出征，是否确认？`
            : `将自动选择 ${plannedTroops.length} 种护院，每种携带 1 个（共 ${plannedTroops.length} 个），并派遣 ${plannedGuests.length} 名门客。是否确认出征？`;
          const options = { title: "快速出征", okText: "确认出征" };
          const confirmed = typeof window.gameConfirm === "function"
            ? await window.gameConfirm(message, options)
            : window.gameDialog?.confirm
              ? await window.gameDialog.confirm(message, options)
              : window.confirm(message);
          if (!confirmed) return;
          guestInputs.forEach((input) => {
            input.checked = plannedGuests.includes(input);
          });
          troopInputs.forEach((input) => {
            input.value = plannedTroops.includes(input) ? "1" : "0";
          });
          delete loadoutForm.dataset.quickPending;
          updateLoadout();
          if (!submitButton || submitButton.disabled) return;
          loadoutForm.dataset.quickPending = "1";
          loadoutForm.requestSubmit(submitButton);
          submitted = true;
        } finally {
          if (!submitted) delete loadoutForm.dataset.quickPending;
          updateLoadout();
        }
      });

      updateLoadout();
    }

    document.querySelectorAll(".js-mission-card-form").forEach((form) => {
      if (form.dataset.submitBound === "1") {
        return;
      }
      form.dataset.submitBound = "1";
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (form.dataset.confirmPending === "1") {
          return;
        }

        const submitButton = form.querySelector('button[type="submit"]');
        const wasDisabled = Boolean(submitButton?.disabled);
        form.dataset.confirmPending = "1";
        if (submitButton) {
          submitButton.disabled = true;
        }

        let submitted = false;
        try {
          const message = window.TasksPageCore?.buildMissionCardConfirmation
            ? window.TasksPageCore.buildMissionCardConfirmation({
                missionName: form.dataset.missionName,
                cardCount: form.dataset.cardCount,
                usedCount: form.dataset.usedCount,
                dailyLimit: form.dataset.dailyLimit,
              })
            : "确定消耗 1 张任务卡，增加 1 次今日挑战次数吗？";
          const options = { title: "使用任务卡", okText: "确认使用" };
          const confirmed = typeof window.gameConfirm === "function"
            ? await window.gameConfirm(message, options)
            : window.gameDialog?.confirm
              ? await window.gameDialog.confirm(message, options)
              : window.confirm(message);
          if (!confirmed) {
            return;
          }

          form.submit();
          submitted = true;
        } finally {
          delete form.dataset.confirmPending;
          if (!submitted && submitButton) {
            submitButton.disabled = wasDisabled;
          }
        }
      });
    });

  };

  document.addEventListener("DOMContentLoaded", initTasksPage);
  document.addEventListener("partial-nav:loaded", initTasksPage);
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    const form = document.querySelector("[data-task-loadout]");
    if (!form) return;
    delete form.dataset.quickPending;
    form.dispatchEvent(new Event("task-loadout:restore"));
  });
})();
