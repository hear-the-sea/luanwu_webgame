(function (globalScope) {
  "use strict";

  const pvpTravel =
    globalScope.PvpTravel ||
    (typeof module !== "undefined" && module.exports ? require("./pvp-travel.js") : null);

  function parseNonNegativeInt(value) {
    const parsed = Number.parseInt(value, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
  }

  function summarizeGuildPvpConfig(guestCapacities, troopCounts) {
    const capacities = Array.from(guestCapacities || []).map(parseNonNegativeInt);
    const troops = Array.from(troopCounts || []).map(parseNonNegativeInt);
    const troopCapacity = capacities.reduce((total, value) => total + value, 0);
    const totalTroops = troops.reduce((total, value) => total + value, 0);
    return {
      selectedGuests: capacities.length,
      troopCapacity,
      totalTroops,
      isOverCapacity: totalTroops > troopCapacity,
    };
  }

  function initGuildPvpPage(doc) {
    const currentDocument = doc || globalScope.document;
    if (!currentDocument) return null;

    const root = currentDocument.querySelector("[data-guild-pvp-page]");
    if (!root) return null;

    const dispatchLimit = Number(root.dataset.dispatchLimit || 0);
    const baseTravelSeconds = Number(root.dataset.pvpBaseSeconds || 0);
    const marchFactor = Number(root.dataset.pvpMarchFactor || 1);
    const timeScale = Number(root.dataset.pvpTimeScale || 1);
    const targetSearch = root.querySelector("[data-target-search]");
    const targetRegionFilter = root.querySelector("[data-target-region-filter]");
    const targetEmpty = root.querySelector("[data-target-empty]");
    const guestCountNode = root.querySelector("[data-selected-guest-count]");
    const capacityStatusNode = root.querySelector("[data-guild-capacity-status]");
    const troopSummaryNode = root.querySelector("[data-guild-troop-summary]");
    const travelArrivalNode = root.querySelector("[data-guild-travel-arrival]");
    const travelReturnNode = root.querySelector("[data-guild-travel-return]");
    const submitButton = root.querySelector("[data-launch-submit]");

    const targetOptions = Array.from(root.querySelectorAll("[data-target-option]"));
    const targetRadios = Array.from(root.querySelectorAll("[data-target-radio]"));
    const guestOptions = Array.from(root.querySelectorAll("[data-guest-option]"));
    const troopInputs = Array.from(root.querySelectorAll("[data-troop-input]"));
    const filterButtons = Array.from(root.querySelectorAll("[data-target-filter]"));

    let activeFilter = "all";
    let selectedTargetId =
      targetRadios.find((radio) => radio.checked && !radio.disabled)?.value || root.dataset.defaultTargetId || "";

    function getOptionRadio(option) {
      return option.querySelector("[data-target-radio]");
    }

    function syncTargetSelection() {
      let hasSelectedEnabledOption = false;
      targetOptions.forEach((option) => {
        const optionId = option.dataset.targetId || "";
        const radio = getOptionRadio(option);
        const isSelected = optionId === selectedTargetId && Boolean(radio) && !radio.disabled;
        if (radio) {
          radio.checked = isSelected;
        }
        option.classList.toggle("is-active", isSelected);
        option.classList.toggle("is-selected-target", isSelected);
        hasSelectedEnabledOption = hasSelectedEnabledOption || isSelected;
      });
      if (!hasSelectedEnabledOption) {
        selectedTargetId = "";
      }

      updateSubmitState();
    }

    function updateTargetVisibility() {
      const query = (targetSearch?.value || "").trim().toLowerCase();
      const region = targetRegionFilter?.value || "";
      let visibleCount = 0;

      targetOptions.forEach((option) => {
        const optionStatus = option.dataset.displayStatus || option.dataset.targetStatus || "";
        const matchesFilter = activeFilter === "all" || optionStatus === activeFilter;
        const matchesRegion = !region || (option.dataset.targetRegion || "") === region;
        const matchesQuery = !query || (option.dataset.targetSearch || "").includes(query);
        const visible = matchesFilter && matchesRegion && matchesQuery;
        option.hidden = !visible;
        if (visible) visibleCount += 1;
      });

      if (targetEmpty) {
        targetEmpty.hidden = visibleCount > 0;
      }

      const currentVisible = targetOptions.some((option) => {
        const radio = getOptionRadio(option);
        return (option.dataset.targetId || "") === selectedTargetId && !option.hidden && Boolean(radio) && !radio.disabled;
      });
      if (!currentVisible) {
        const firstVisible = targetOptions.find((option) => {
          const radio = getOptionRadio(option);
          return !option.hidden && Boolean(radio) && !radio.disabled;
        });
        selectedTargetId = firstVisible?.dataset.targetId || "";
      }
      syncTargetSelection();
    }

    function updateGuestCount() {
      return updateConfigurationSummary();
    }

    function checkedGuests() {
      return guestOptions.filter((input) => input.checked);
    }

    function readConfigurationState() {
      return summarizeGuildPvpConfig(
        checkedGuests().map((input) => input.dataset.troopCapacity),
        troopInputs.map((input) => input.value)
      );
    }

    function readTravelEstimate() {
      if (!pvpTravel) return null;
      return pvpTravel.calculatePvpTravelTime({
        routeSeconds: baseTravelSeconds,
        guestAgilities: checkedGuests().map((input) => input.dataset.agility),
        troopCounts: troopInputs.map((input) => input.value),
        externalFactor: marchFactor,
        timeScale,
      });
    }

    function updateConfigurationSummary() {
      const state = readConfigurationState();
      if (guestCountNode) {
        guestCountNode.textContent = String(state.selectedGuests);
      }
      if (troopSummaryNode) {
        troopSummaryNode.textContent = `${state.totalTroops} / ${state.troopCapacity}`;
      }
      if (capacityStatusNode) {
        if (state.selectedGuests === 0) {
          capacityStatusNode.textContent = "选择门客后计算带兵上限。";
        } else if (state.isOverCapacity) {
          capacityStatusNode.textContent = `已超出带兵上限 ${state.totalTroops - state.troopCapacity} 人。`;
        } else {
          capacityStatusNode.textContent = `当前阵容还可携带 ${state.troopCapacity - state.totalTroops} 人。`;
        }
        capacityStatusNode.classList?.toggle("is-over-limit", state.isOverCapacity);
        capacityStatusNode.setAttribute?.("role", state.isOverCapacity ? "alert" : "status");
      }

      const travelEstimate = readTravelEstimate();
      if (travelEstimate) {
        if (travelArrivalNode) {
          travelArrivalNode.textContent = pvpTravel.formatDuration(travelEstimate.scaledSeconds);
        }
        if (travelReturnNode) {
          travelReturnNode.textContent = pvpTravel.formatDuration(travelEstimate.scaledSeconds * 2);
        }
      }
      updateSubmitState(state);
      return state;
    }

    function clampTroopValue(input, nextValue) {
      const max = Number(input.max || 0);
      const value = Math.max(0, Math.min(max, parseNonNegativeInt(nextValue)));
      input.value = String(value);
    }

    function updateSubmitState(configurationState) {
      const hasTarget = targetRadios.some((radio) => radio.checked && !radio.disabled);
      const hasGuests = guestOptions.some((input) => input.checked);
      const state = configurationState || readConfigurationState();
      if (submitButton) {
        submitButton.disabled = !(hasTarget && hasGuests && !state.isOverCapacity);
      }
    }

    targetOptions.forEach((option) => {
      option.addEventListener("click", () => {
        const radio = getOptionRadio(option);
        if (!radio || radio.disabled) return;
        selectedTargetId = option.dataset.targetId || "";
        syncTargetSelection();
      });
    });

    targetRadios.forEach((radio) => {
      radio.addEventListener("change", () => {
        if (radio.disabled || !radio.checked) return;
        selectedTargetId = radio.value || "";
        syncTargetSelection();
      });
    });

    filterButtons.forEach((button) => {
      button.addEventListener("click", () => {
        activeFilter = button.dataset.targetFilter || "all";
        filterButtons.forEach((node) => node.classList.toggle("is-active", node === button));
        updateTargetVisibility();
      });
    });

    targetSearch?.addEventListener("input", updateTargetVisibility);
    targetRegionFilter?.addEventListener("change", updateTargetVisibility);

    guestOptions.forEach((input) => {
      input.addEventListener("change", () => {
        if (dispatchLimit > 0) {
          const checkedCount = guestOptions.filter((node) => node.checked).length;
          if (checkedCount > dispatchLimit) {
            input.checked = false;
          }
        }
        updateGuestCount();
      });
    });

    troopInputs.forEach((input) => {
      input.addEventListener("input", () => {
        clampTroopValue(input, input.value);
        updateConfigurationSummary();
      });
    });

    root.querySelectorAll("[data-adjust-troop]").forEach((button) => {
      button.addEventListener("click", () => {
        const key = button.dataset.troopKey || "";
        const input = troopInputs.find((node) => node.dataset.troopKey === key);
        if (!input) return;
        clampTroopValue(input, Number(input.value || 0) + Number(button.dataset.adjustTroop || 0));
        updateConfigurationSummary();
      });
    });

    root.querySelectorAll("[data-fill-troop]").forEach((button) => {
      button.addEventListener("click", () => {
        const key = button.dataset.fillTroop || "";
        const input = troopInputs.find((node) => node.dataset.troopKey === key);
        if (!input) return;
        clampTroopValue(input, Number(input.max || 0));
        updateConfigurationSummary();
      });
    });

    updateTargetVisibility();
    updateGuestCount();

    return {
      updateGuestCount,
      readConfigurationState,
      readTravelEstimate,
      updateConfigurationSummary,
      updateTargetVisibility,
    };
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { initGuildPvpPage, summarizeGuildPvpConfig };
  }

  if (globalScope.document) {
    initGuildPvpPage(globalScope.document);
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
