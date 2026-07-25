(function (globalScope) {
  "use strict";

  function parseNonNegativeInt(value) {
    const parsed = Number.parseInt(value, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
  }

  function summarizeRaidConfig(guestCapacities, troopCounts) {
    const capacities = Array.from(guestCapacities || []).map(parseNonNegativeInt);
    const troops = Array.from(troopCounts || []).map(parseNonNegativeInt);
    const troopCapacity = capacities.reduce((total, value) => total + value, 0);
    const totalTroops = troops.reduce((total, value) => total + value, 0);
    const selectedGuests = capacities.length;
    const isOverCapacity = totalTroops > troopCapacity;

    return {
      selectedGuests,
      troopCapacity,
      totalTroops,
      isOverCapacity,
      canSubmit: selectedGuests > 0 && !isOverCapacity,
    };
  }

  function resolveTroopValue({ current, requested, inventoryMax, otherTroops, capacity }) {
    const max = parseNonNegativeInt(inventoryMax);
    const normalizedCurrent = Math.min(parseNonNegativeInt(current), max);
    const normalizedRequested = Math.min(parseNonNegativeInt(requested), max);

    if (normalizedRequested <= normalizedCurrent) {
      return normalizedRequested;
    }

    const remainingForInput = Math.max(0, parseNonNegativeInt(capacity) - parseNonNegativeInt(otherTroops));
    return Math.min(normalizedRequested, Math.max(normalizedCurrent, remainingForInput));
  }

  function initRaidConfigPage(doc, runtime) {
    const currentDocument = doc || globalScope.document;
    const scope = runtime || globalScope;
    if (!currentDocument || typeof currentDocument.querySelector !== "function") {
      return null;
    }

    const form = currentDocument.querySelector("[data-raid-config-page]");
    if (!form || form.dataset.raidConfigInitialized === "1") {
      return null;
    }
    form.dataset.raidConfigInitialized = "1";

    const raidApiUrl = form.dataset.raidApiUrl || "";
    const mapUrl = form.dataset.mapUrl || "";
    const targetId = Number.parseInt(form.dataset.targetId || "", 10);
    const maxSquadSize = parseNonNegativeInt(form.dataset.maxSquadSize);

    const guestInputs = Array.from(form.querySelectorAll("[data-raid-guest]"));
    const troopInputs = Array.from(form.querySelectorAll("[data-raid-troop]"));
    const adjustButtons = Array.from(form.querySelectorAll("[data-raid-adjust]"));
    const fillButtons = Array.from(form.querySelectorAll("[data-raid-fill]"));
    const selectedCount = form.querySelector("[data-raid-selected-count]");
    const summaryGuests = form.querySelector("[data-raid-summary-guests]");
    const summaryTroops = form.querySelector("[data-raid-summary-troops]");
    const troopCapacity = form.querySelector("[data-raid-troop-capacity]");
    const capacityStatus = form.querySelector("[data-raid-capacity-status]");
    const submitBtn = form.querySelector("[data-raid-submit]");
    const selectMaxBtn = form.querySelector("[data-raid-select-max]");
    const clearGuestsBtn = form.querySelector("[data-raid-clear-guests]");
    const clearTroopsBtn = form.querySelector("[data-raid-clear-troops]");

    if (!selectedCount || !summaryGuests || !summaryTroops || !troopCapacity || !capacityStatus || !submitBtn) {
      return null;
    }

    const idleSubmitLabel = submitBtn.textContent.trim() || "发起进攻";
    let isSubmitting = false;

    function getCSRFToken() {
      const meta = currentDocument.querySelector('meta[name="csrf-token"]');
      const metaToken = meta ? meta.getAttribute("content") : "";
      if (metaToken && metaToken !== "NOTPROVIDED") {
        return metaToken;
      }

      const input = currentDocument.querySelector('input[name="csrfmiddlewaretoken"]');
      if (input && input.value) {
        return input.value;
      }

      const cookie = (currentDocument.cookie || "")
        .split("; ")
        .find((row) => row.startsWith("csrftoken="));
      return cookie ? decodeURIComponent(cookie.split("=")[1]) : "";
    }

    function showAlert(message, options) {
      if (typeof scope.gameAlert === "function") {
        return scope.gameAlert(message, options);
      }
      if (typeof scope.alert === "function") {
        scope.alert(message);
      }
      return Promise.resolve();
    }

    function showError(message, options) {
      if (scope.gameDialog && typeof scope.gameDialog.error === "function") {
        return scope.gameDialog.error(message, options);
      }
      return showAlert(message, options);
    }

    function showSuccess(message, options) {
      if (scope.gameDialog && typeof scope.gameDialog.success === "function") {
        return scope.gameDialog.success(message, options);
      }
      return showAlert(message, options);
    }

    function checkedGuests() {
      return guestInputs.filter((input) => input.checked);
    }

    function readState() {
      return summarizeRaidConfig(
        checkedGuests().map((input) => input.dataset.troopCapacity),
        troopInputs.map((input) => input.value)
      );
    }

    function updateSummary() {
      const state = readState();
      selectedCount.textContent = String(state.selectedGuests);
      summaryGuests.textContent = `${state.selectedGuests} 人`;
      summaryTroops.textContent = String(state.totalTroops);
      troopCapacity.textContent = String(state.troopCapacity);

      if (state.selectedGuests === 0) {
        capacityStatus.textContent = "选择门客后计算带兵上限";
      } else if (state.isOverCapacity) {
        capacityStatus.textContent = `已超出带兵上限 ${state.totalTroops - state.troopCapacity} 人`;
      } else {
        capacityStatus.textContent = `还可携带 ${state.troopCapacity - state.totalTroops} 人`;
      }
      capacityStatus.classList.toggle("is-over-limit", state.isOverCapacity);
      capacityStatus.setAttribute("role", state.isOverCapacity ? "alert" : "status");
      submitBtn.disabled = !state.canSubmit || isSubmitting;
      return state;
    }

    function setSubmitting(nextSubmitting) {
      isSubmitting = Boolean(nextSubmitting);
      submitBtn.textContent = isSubmitting ? "发起进攻中…" : idleSubmitLabel;
      updateSummary();
    }

    function troopInputByKey(troopKey) {
      return troopInputs.find((input) => input.dataset.troopKey === troopKey);
    }

    function otherTroopTotal(currentInput) {
      return troopInputs.reduce(
        (total, input) => total + (input === currentInput ? 0 : parseNonNegativeInt(input.value)),
        0
      );
    }

    function inventoryMax(input) {
      return input.dataset.max || input.max || "0";
    }

    function setTroopValue(input, requested, current) {
      const state = readState();
      const nextValue = resolveTroopValue({
        current,
        requested,
        inventoryMax: inventoryMax(input),
        otherTroops: otherTroopTotal(input),
        capacity: state.troopCapacity,
      });
      input.value = String(nextValue);
      input.dataset.previousValue = String(nextValue);
      updateSummary();
    }

    guestInputs.forEach((input) => {
      input.addEventListener("change", () => {
        if (maxSquadSize > 0 && checkedGuests().length > maxSquadSize) {
          input.checked = false;
          void showAlert(`最多只能选择 ${maxSquadSize} 名门客出征`, { title: "选择限制" });
        }
        updateSummary();
      });
    });

    if (selectMaxBtn) {
      selectMaxBtn.addEventListener("click", () => {
        let remaining = maxSquadSize > 0 ? maxSquadSize : guestInputs.length;
        guestInputs.forEach((input) => {
          if (!input.checked) return;
          if (remaining > 0) {
            remaining -= 1;
          } else {
            input.checked = false;
          }
        });
        guestInputs.forEach((input) => {
          if (!input.checked && remaining > 0) {
            input.checked = true;
            remaining -= 1;
          }
        });
        updateSummary();
      });
    }

    if (clearGuestsBtn) {
      clearGuestsBtn.addEventListener("click", () => {
        guestInputs.forEach((input) => {
          input.checked = false;
        });
        updateSummary();
      });
    }

    adjustButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const input = troopInputByKey(button.dataset.troopKey || "");
        if (!input) return;
        const current = parseNonNegativeInt(input.value);
        const adjustment = Number.parseInt(button.dataset.raidAdjust || "0", 10) || 0;
        setTroopValue(input, current + adjustment, current);
      });
    });

    fillButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const input = troopInputByKey(button.dataset.raidFill || "");
        if (!input) return;
        const current = parseNonNegativeInt(input.value);
        setTroopValue(input, inventoryMax(input), current);
      });
    });

    troopInputs.forEach((input) => {
      input.dataset.previousValue = String(parseNonNegativeInt(input.value));
      input.addEventListener("change", () => {
        const previous = parseNonNegativeInt(input.dataset.previousValue);
        setTroopValue(input, input.value, previous);
      });
    });

    if (clearTroopsBtn) {
      clearTroopsBtn.addEventListener("click", () => {
        troopInputs.forEach((input) => {
          input.value = "0";
          input.dataset.previousValue = "0";
        });
        updateSummary();
      });
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const state = readState();
      const selectedGuestIds = checkedGuests().map((input) => Number.parseInt(input.value, 10));
      if (!selectedGuestIds.length) {
        await showAlert("请至少选择一名门客出征", { title: "提示" });
        return;
      }
      if (state.isOverCapacity) {
        await showError("携带护院已超过当前阵容的带兵上限", { title: "配置超限" });
        return;
      }
      if (!raidApiUrl || !Number.isFinite(targetId) || typeof scope.fetch !== "function") {
        await showError("出征配置暂不可用，请刷新页面后重试", { title: "错误" });
        return;
      }

      const troopLoadout = {};
      troopInputs.forEach((input) => {
        const count = parseNonNegativeInt(input.value);
        if (count > 0) {
          troopLoadout[input.dataset.troopKey] = count;
        }
      });

      setSubmitting(true);
      let succeeded = false;
      try {
        const response = await scope.fetch(raidApiUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCSRFToken(),
          },
          body: JSON.stringify({
            target_id: targetId,
            guest_ids: selectedGuestIds,
            troop_loadout: troopLoadout,
          }),
        });
        const data = await response.json();
        if (data.success) {
          succeeded = true;
          await showSuccess(data.message, { title: "出征成功" });
          if (scope.location) {
            scope.location.href = mapUrl || "/manor/map/";
          }
          return;
        }

        await showError(`出征失败: ${data.error || "未知错误"}`, { title: "出征失败" });
      } catch (error) {
        if (scope.console && typeof scope.console.error === "function") {
          scope.console.error("Raid request failed:", error);
        }
        await showError("请求失败，请稍后重试", { title: "错误" });
      } finally {
        if (!succeeded) {
          setSubmitting(false);
        }
      }
    });

    updateSummary();
    return { readState, updateSummary };
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      initRaidConfigPage,
      parseNonNegativeInt,
      resolveTroopValue,
      summarizeRaidConfig,
    };
  }

  if (globalScope.document) {
    initRaidConfigPage(globalScope.document);
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
