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
        if (window.history && window.history.replaceState && window.TasksPageCore?.buildTaskTabUrl) {
          window.history.replaceState({}, "", window.TasksPageCore.buildTaskTabUrl(window.location.href, tabId));
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
