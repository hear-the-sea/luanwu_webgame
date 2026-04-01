(() => {
  const initTasksPage = () => {
    if (!document.querySelector(".tw-mission-tabs")) {
      return;
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
      });
    });

    const selectedCountEls = document.querySelectorAll("[id='selected-guest-count']");
    const guestInputs = document.querySelectorAll(".guest-input");

    const updateGuestCount = (scope) => {
      if (!selectedCountEls.length) {
        return;
      }
      selectedCountEls.forEach((element) => {
        const container = scope || element.closest("form") || document;
        const count = container.querySelectorAll(".guest-input:checked").length;
        element.textContent = String(count);
      });
    };

    guestInputs.forEach((input) => {
      if (input.dataset.changeBound === "1") {
        return;
      }
      input.dataset.changeBound = "1";
      input.addEventListener("change", () => {
        const form = input.closest("form") || document;
        const selectedCountEl = form.querySelector("[id='selected-guest-count']") || selectedCountEls[0];
        const maxSquadSize = Number.parseInt(selectedCountEl?.dataset?.maxSquad || "0", 10)
          || Number.parseInt(document.body.dataset.maxMissionSquad || "0", 10)
          || 5;
        const selectedGuests = form.querySelectorAll(".guest-input:checked").length;
        if (selectedGuests > maxSquadSize) {
          input.checked = false;
          if (typeof window.gameAlert === "function") {
            window.gameAlert(`最多只能选择 ${maxSquadSize} 名门客出征`, { title: "选择限制" });
          } else {
            window.alert(`最多只能选择 ${maxSquadSize} 名门客出征`);
          }
          return;
        }
        updateGuestCount(form);
      });
    });

    document.querySelectorAll(".tw-troop-slider").forEach((slider) => {
      if (slider.dataset.inputBound === "1") {
        return;
      }
      slider.dataset.inputBound = "1";
      slider.addEventListener("input", () => {
        const form = slider.closest("form") || document;
        const troopKey = slider.dataset.troopKey;
        const numInput = form.querySelector(`.tw-troop-num-input[data-troop-key="${troopKey}"]`);
        if (numInput) {
          numInput.value = slider.value;
        }
      });
    });

    document.querySelectorAll(".tw-troop-num-input").forEach((input) => {
      if (input.dataset.inputBound !== "1") {
        input.dataset.inputBound = "1";
        input.addEventListener("input", () => {
          const form = input.closest("form") || document;
          const troopKey = input.dataset.troopKey;
          const max = Number.parseInt(input.dataset.max || "0", 10) || 0;
          let value = Number.parseInt(input.value, 10) || 0;
          value = Math.max(0, Math.min(max, value));
          const slider = form.querySelector(`.tw-troop-slider[data-troop-key="${troopKey}"]`);
          if (slider) {
            slider.value = String(value);
          }
        });
      }
      if (input.dataset.blurBound !== "1") {
        input.dataset.blurBound = "1";
        input.addEventListener("blur", () => {
          const max = Number.parseInt(input.dataset.max || "0", 10) || 0;
          let value = Number.parseInt(input.value, 10) || 0;
          value = Math.max(0, Math.min(max, value));
          input.value = String(value);
        });
      }
    });

    updateGuestCount();
  };

  document.addEventListener("DOMContentLoaded", initTasksPage);
  document.addEventListener("partial-nav:loaded", initTasksPage);
})();
