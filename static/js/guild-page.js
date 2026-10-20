(() => {
  const bindOnce = (element, key) => {
    if (!element || element.dataset[key] === "1") {
      return false;
    }
    element.dataset[key] = "1";
    return true;
  };

  const openModal = (modal, trigger = null) => {
    if (!modal) {
      return false;
    }
    if (typeof window.GuildModal?.open === "function") {
      return window.GuildModal.open(modal, trigger);
    }
    modal.style.display = "flex";
    modal.setAttribute("aria-hidden", "false");
    return true;
  };

  const confirmDanger = async (message, options) => {
    if (typeof window.gameDialog?.danger === "function") {
      return window.gameDialog.danger(message, options);
    }
    return window.confirm(message);
  };

  const initApplications = () => {
    const modal = document.getElementById("reject-modal");
    const form = document.getElementById("reject-form");
    const target = document.getElementById("reject-target");
    if (!modal || !form || !target) {
      return;
    }

    document.querySelectorAll(".js-reject-application").forEach((button) => {
      if (!bindOnce(button, "guildRejectBound")) {
        return;
      }
      button.addEventListener("click", () => {
        form.action = button.dataset.rejectUrl || "";
        target.textContent = `确认拒绝 ${button.dataset.applicantName || "该玩家"} 的申请吗？`;
        openModal(modal, button);
      });
    });
  };

  const bindConfirmationForms = (selector, message, title) => {
    document.querySelectorAll(selector).forEach((form) => {
      if (!bindOnce(form, "guildConfirmBound")) {
        return;
      }
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
          const name = form.dataset.name || "该成员";
          const confirmed = await confirmDanger(message(name), { title });
          if (confirmed) {
            HTMLFormElement.prototype.submit.call(form);
          }
        } catch (error) {
          console.error(`${title}确认失败:`, error);
        }
      });
    });
  };

  const initMemberActions = () => {
    bindConfirmationForms(
      ".transfer-form",
      (name) => `确认将帮主之位传给 ${name} 吗？`,
      "转让帮主",
    );
    bindConfirmationForms(
      ".kick-form",
      (name) => `确认辞退 ${name} 吗？`,
      "辞退成员",
    );
  };

  const initGuildDetail = () => {
    const donationModal = document.getElementById("guild-donation-modal");
    const donationKindSelect = document.getElementById("guild-donation-kind");
    const resourceDonationForm = document.getElementById("guild-resource-donation-form");
    const troopDonationForm = document.getElementById("guild-troop-donation-form");
    const resourceTypeInput = document.getElementById("resource_type");
    const resourcePanels = Array.from(document.querySelectorAll("[data-resource-panel]"));
    const troopKeyInput = document.getElementById("troop_key");
    const troopQuantityInput = document.getElementById("troop_quantity");
    const troopContributionPreview = document.getElementById("troop-contribution-preview");
    const troopDonationSubmit = document.getElementById("troop-donation-submit");

    if (!donationModal || !donationKindSelect || !resourceDonationForm || !troopDonationForm) {
      return;
    }

    const syncTroopDonationLimit = () => {
      if (!troopKeyInput || !troopQuantityInput) {
        return;
      }
      const selected = troopKeyInput.selectedOptions[0];
      const rate = Number.parseInt(selected?.dataset.contributionRate || "0", 10);
      const maxQuantity = Number.parseInt(selected?.dataset.maxQuantity || "0", 10);
      troopQuantityInput.max = String(Math.max(0, maxQuantity));
      if (maxQuantity <= 0) {
        troopQuantityInput.value = "";
      } else {
        const currentQuantity = Number.parseInt(troopQuantityInput.value || "1", 10);
        troopQuantityInput.value = String(Math.min(Math.max(1, currentQuantity), maxQuantity));
      }
      if (troopContributionPreview) {
        troopContributionPreview.textContent = maxQuantity > 0
          ? `每名 ${rate} 贡献，本次最多可捐 ${maxQuantity} 名`
          : "今日护院贡献额度不足";
      }
      if (troopDonationSubmit) {
        troopDonationSubmit.disabled = maxQuantity <= 0;
      }
    };

    const syncGuildDonationForms = () => {
      const kind = donationKindSelect.value;
      const isTroops = kind === "troops";
      resourceDonationForm.style.display = isTroops ? "none" : "block";
      troopDonationForm.style.display = isTroops ? "block" : "none";
      if (troopKeyInput) {
        troopKeyInput.disabled = !isTroops;
        troopKeyInput.required = isTroops;
      }
      if (troopQuantityInput) {
        troopQuantityInput.disabled = !isTroops;
        troopQuantityInput.required = isTroops;
      }
      if (isTroops) {
        syncTroopDonationLimit();
      }
      resourcePanels.forEach((panel) => {
        const active = !isTroops && panel.dataset.resourcePanel === kind;
        panel.hidden = !active;
        panel.querySelectorAll('input[name="amount"]').forEach((input) => {
          input.disabled = !active;
          input.required = active;
        });
      });
      if (!isTroops && resourceTypeInput) {
        resourceTypeInput.value = kind;
      }
    };

    if (bindOnce(donationKindSelect, "guildDonationBound")) {
      donationKindSelect.addEventListener("change", syncGuildDonationForms);
    }
    if (bindOnce(troopKeyInput, "guildDonationBound")) {
      troopKeyInput.addEventListener("change", syncTroopDonationLimit);
    }
    document.querySelectorAll('[data-guild-modal-open="guild-donation-modal"]').forEach((button) => {
      if (bindOnce(button, "guildDonationOpenBound")) {
        button.addEventListener("click", syncGuildDonationForms);
      }
    });

    if (new URLSearchParams(window.location?.search || "").get("donate") === "1") {
      openModal(donationModal);
      syncGuildDonationForms();
    }
  };

  const initLeaveAction = () => {
    const form = document.getElementById("leave-guild-form");
    if (!form || !bindOnce(form, "guildLeaveBound")) {
      return;
    }
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const confirmed = await confirmDanger(
          "确认退出帮会吗？退出后当前贡献将清零。",
          { title: "退出帮会" },
        );
        if (confirmed) {
          HTMLFormElement.prototype.submit.call(form);
        }
      } catch (error) {
        console.error("退出帮会确认失败:", error);
      }
    });
  };

  const init = () => {
    initApplications();
    initMemberActions();
    initGuildDetail();
    initLeaveAction();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
  document.addEventListener("partial-nav:loaded", init);
})();
