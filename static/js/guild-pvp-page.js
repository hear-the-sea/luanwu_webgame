(function () {
  const root = document.querySelector("[data-guild-pvp-page]");
  if (!root) return;

  const dispatchLimit = Number(root.dataset.dispatchLimit || 0);
  const targetSearch = root.querySelector("[data-target-search]");
  const targetRegionFilter = root.querySelector("[data-target-region-filter]");
  const targetEmpty = root.querySelector("[data-target-empty]");
  const guestCountNode = root.querySelector("[data-selected-guest-count]");
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
      const matchesFilter = activeFilter === "all" || option.dataset.targetStatus === activeFilter;
      const matchesRegion = !region || (option.dataset.targetRegion || "") === region;
      const matchesQuery = !query || (option.dataset.targetSearch || "").includes(query);
      const visible = matchesFilter && matchesRegion && matchesQuery;
      option.hidden = !visible;
      if (visible) visibleCount += 1;
    });

    if (targetEmpty) {
      targetEmpty.hidden = visibleCount > 0;
    }

    const currentVisible = targetOptions.some(
      (option) => {
        const radio = getOptionRadio(option);
        return (option.dataset.targetId || "") === selectedTargetId && !option.hidden && Boolean(radio) && !radio.disabled;
      }
    );
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
    const checked = guestOptions.filter((input) => input.checked);
    if (guestCountNode) {
      guestCountNode.textContent = String(checked.length);
    }
    updateSubmitState();
  }

  function clampTroopValue(input, nextValue) {
    const max = Number(input.max || 0);
    const value = Math.max(0, Math.min(max, Number(nextValue || 0)));
    input.value = String(value);
  }

  function updateSubmitState() {
    const hasTarget = targetRadios.some((radio) => radio.checked && !radio.disabled);
    const hasGuests = guestOptions.some((input) => input.checked);
    if (submitButton) {
      submitButton.disabled = !(hasTarget && hasGuests);
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
          return;
        }
      }
      updateGuestCount();
    });
  });

  troopInputs.forEach((input) => {
    input.addEventListener("input", () => {
      clampTroopValue(input, input.value);
    });
  });

  root.querySelectorAll("[data-adjust-troop]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.troopKey || "";
      const input = troopInputs.find((node) => node.dataset.troopKey === key);
      if (!input) return;
      clampTroopValue(input, Number(input.value || 0) + Number(button.dataset.adjustTroop || 0));
    });
  });

  root.querySelectorAll("[data-fill-troop]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.fillTroop || "";
      const input = troopInputs.find((node) => node.dataset.troopKey === key);
      if (!input) return;
      clampTroopValue(input, Number(input.max || 0));
    });
  });

  updateTargetVisibility();
  updateGuestCount();
})();
