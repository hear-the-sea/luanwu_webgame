const initGuestRosterPage = () => {
  const rosterRoot = document.querySelector(".dashboard");
  if (!rosterRoot) {
    return;
  }
  if (rosterRoot.dataset.pageInitialized === "1") {
    return;
  }
  rosterRoot.dataset.pageInitialized = "1";

  const showError = (message) => {
    let container = document.querySelector(".flash-messages");
    if (!container) {
      container = document.createElement("ul");
      container.className = "flash-messages";
      const main = document.querySelector("main");
      if (main) {
        main.insertBefore(container, main.firstChild);
      } else {
        document.body.insertBefore(container, document.body.firstChild);
      }
    }

    container.querySelectorAll(".flash.ajax-error").forEach((element) => element.remove());

    const messageElement = document.createElement("li");
    messageElement.className = "flash error ajax-error";
    messageElement.textContent = message;
    container.appendChild(messageElement);

    window.setTimeout(() => {
      messageElement.remove();
      if (container.children.length === 0) {
        container.remove();
      }
    }, 5000);
  };

  const updateItemQuantity = (selectElem, itemId, newQuantity) => {
    const targetId = String(itemId ?? "");
    const option =
      Array.from(selectElem.options).find((entry) => String(entry.value) === targetId)
      || selectElem.selectedOptions?.[0]
      || null;
    if (!option) {
      return;
    }

    const submitBtn = selectElem.closest("form")?.querySelector('button[type="submit"]');
    const parsed = Number.parseInt(newQuantity, 10);
    const quantity = Number.isFinite(parsed) ? Math.max(parsed, 0) : 0;

    if (quantity <= 0) {
      const wasSelected = option.selected;
      option.remove();
      if (selectElem.options.length === 0) {
        if (submitBtn) {
          submitBtn.disabled = true;
        }
        return;
      }
      if (wasSelected) {
        selectElem.selectedIndex = 0;
      }
      if (submitBtn) {
        submitBtn.disabled = false;
      }
      return;
    }

    const itemName = option.dataset.itemName || option.textContent.replace(/（x\d+）/, "").trim();
    option.dataset.itemName = itemName;
    option.textContent = `${itemName}（×${quantity}）`;
    if (submitBtn) {
      submitBtn.disabled = false;
    }
  };

  const updateGuestTrainingState = (guestId, trainingEta, trainingPaused) => {
    const row = document.querySelector(`tr[data-guest-id="${guestId}"]`);
    const upgradeCell = row?.querySelector(".guest-col-upgrade");
    if (!upgradeCell) {
      return;
    }

    upgradeCell.textContent = "";
    if (trainingPaused) {
      const span = document.createElement("span");
      span.className = "tw-muted";
      span.title = "门客恢复空闲后继续训练";
      span.textContent = "已暂停";
      upgradeCell.appendChild(span);
      return;
    }

    if (trainingEta) {
      const span = document.createElement("span");
      span.className = "countdown";
      span.setAttribute("data-countdown", trainingEta);
      span.setAttribute("data-format", "zh");
      span.setAttribute("data-check-url", `/guests/${guestId}/check-training/`);
      span.textContent = "计算中";
      upgradeCell.appendChild(span);
      return;
    }

    const span = document.createElement("span");
    span.className = "tw-muted";
    span.textContent = "自动升级";
    upgradeCell.appendChild(span);
  };

  const updateGuestRow = (guestId, newLevel, trainingEta, currentHp, maxHp) => {
    const row = document.querySelector(`tr[data-guest-id="${guestId}"]`);
    if (!row) {
      return;
    }

    const levelDiv = row.querySelector(".guest-level");
    if (levelDiv) {
      levelDiv.textContent = `等级 ${newLevel}`;
    }

    if (currentHp !== undefined && maxHp !== undefined) {
      const hpDiv = row.querySelector(".guest-hp");
      if (hpDiv) {
        hpDiv.textContent = `生命 ${currentHp}/${maxHp}`;
      }
    }

    updateGuestTrainingState(guestId, trainingEta, false);
  };

  const salaryModal = document.getElementById("salary-modal");
  const salaryForm = document.getElementById("salary-form");
  const salaryGuestName = document.getElementById("salary-guest-name");
  const salaryAmount = document.getElementById("salary-amount");
  const salaryConfirmBtn = document.getElementById("salary-confirm-btn");

  const openSalaryModal = (guestId, guestName, salary, canPay) => {
    if (!salaryModal || !salaryForm || !salaryGuestName || !salaryAmount || !salaryConfirmBtn) {
      return;
    }
    salaryGuestName.textContent = guestName;
    salaryAmount.textContent = `${salary} 银两`;
    salaryForm.action = `/guests/${guestId}/pay-salary/`;
    salaryConfirmBtn.disabled = !canPay;
    salaryModal.style.display = "flex";
  };

  const closeSalaryModal = () => {
    if (salaryModal) {
      salaryModal.style.display = "none";
    }
  };

  document.querySelectorAll(".open-salary-modal").forEach((button) => {
    button.addEventListener("click", () => {
      openSalaryModal(
        button.dataset.guestId,
        button.dataset.guestName,
        button.dataset.salary,
        button.dataset.canPay === "true",
      );
    });
  });
  document.querySelectorAll(".close-salary-modal").forEach((button) => {
    button.addEventListener("click", closeSalaryModal);
  });
  salaryModal?.addEventListener("click", (event) => {
    if (event.target === salaryModal) {
      closeSalaryModal();
    }
  });

  const payAllForm = document.getElementById("pay-all-salary-form");
  if (payAllForm) {
    payAllForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        const amount = payAllForm.dataset.amount;
        const confirmHandler =
          typeof window.gameConfirm === "function"
            ? window.gameConfirm
            : (message, _options) => Promise.resolve(window.confirm(message));
        const confirmed = await confirmHandler(`确认支付所有门客工资共计 ${amount} 银两？`, { title: "支付工资" });
        if (confirmed) {
          payAllForm.submit();
        }
      } catch (error) {
        console.error("支付工资确认失败:", error);
      }
    });
  }

  const healAllForm = document.getElementById("heal-all-guests-form");
  if (healAllForm) {
    healAllForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const guestCount = healAllForm.dataset.count || "0";
      const medicineQuantity = healAllForm.dataset.medicineQuantity || "0";
      const confirmHandler =
        typeof window.gameConfirm === "function"
          ? window.gameConfirm
          : (message, _options) => Promise.resolve(window.confirm(message));
      try {
        const confirmed = await confirmHandler(
          `将按重伤优先、缺血比例优先的顺序治疗 ${guestCount} 位门客，最多消耗 ${medicineQuantity} 个疗伤道具。确认继续？`,
          { title: "一键疗伤" },
        );
        if (confirmed) {
          healAllForm.submit();
        }
      } catch (error) {
        console.error("一键疗伤确认失败:", error);
      }
    });
  }

  const expModal = document.getElementById("exp-item-modal");
  const expForm = document.getElementById("exp-item-form");
  const expTargetLabel = document.getElementById("exp-modal-target");

  const toggleExpModal = (show) => {
    if (expModal) {
      expModal.style.display = show ? "flex" : "none";
    }
  };

  document.querySelectorAll(".open-exp-modal").forEach((button) => {
    button.addEventListener("click", () => {
      if (expForm && button.dataset.guest) {
        expForm.action = button.dataset.url || "";
      }
      if (expTargetLabel) {
        const guestName = button.dataset.guestName || "";
        expTargetLabel.textContent = guestName ? `目标：${guestName}` : "";
      }
      toggleExpModal(true);
    });
  });
  document.querySelectorAll(".close-exp-modal").forEach((button) => {
    button.addEventListener("click", () => toggleExpModal(false));
  });
  expModal?.addEventListener("click", (event) => {
    if (event.target === expModal) {
      toggleExpModal(false);
    }
  });

  if (expForm) {
    expForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formData = new FormData(expForm);
      const submitBtn = expForm.querySelector('button[type="submit"]');
      if (submitBtn) {
        submitBtn.disabled = true;
      }

      try {
        const response = await fetch(expForm.action, {
          method: "POST",
          body: formData,
          headers: { "X-Requested-With": "XMLHttpRequest" },
        });
        const data = await response.json();
        if (!data.success) {
          showError(data.message);
        } else {
          const select = expForm.querySelector('select[name="item_id"]');
          if (select) {
            updateItemQuantity(select, data.item_id, data.new_quantity);
          }
          if (data.guest_id && data.new_level !== undefined) {
            updateGuestRow(data.guest_id, data.new_level, data.training_eta, data.current_hp, data.max_hp);
          }
        }
      } catch (_error) {
        showError("请求失败，请重试");
      } finally {
        if (submitBtn) {
          submitBtn.disabled = false;
        }
      }
    });
  }

  const medicineModal = document.getElementById("medicine-modal");
  const medicineForm = document.getElementById("medicine-form");
  const medicineTarget = document.getElementById("medicine-modal-target");

  const toggleMedicineModal = (show) => {
    if (medicineModal) {
      medicineModal.style.display = show ? "flex" : "none";
    }
  };

  document.querySelectorAll(".open-medicine-modal").forEach((button) => {
    button.addEventListener("click", () => {
      if (medicineForm) {
        medicineForm.action = button.dataset.url || "";
      }
      if (medicineTarget) {
        const guestName = button.dataset.guestName || "";
        medicineTarget.textContent = guestName ? `目标：${guestName}` : "";
      }
      toggleMedicineModal(true);
    });
  });
  document.querySelectorAll(".close-medicine-modal").forEach((button) => {
    button.addEventListener("click", () => toggleMedicineModal(false));
  });
  medicineModal?.addEventListener("click", (event) => {
    if (event.target === medicineModal) {
      toggleMedicineModal(false);
    }
  });

  if (medicineForm) {
    medicineForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formData = new FormData(medicineForm);
      const submitBtn = medicineForm.querySelector('button[type="submit"]');
      if (submitBtn) {
        submitBtn.disabled = true;
      }

      try {
        const response = await fetch(medicineForm.action, {
          method: "POST",
          body: formData,
          headers: { "X-Requested-With": "XMLHttpRequest" },
        });
        const data = await response.json();
        if (!data.success) {
          showError(data.message);
        } else {
          const select = medicineForm.querySelector('select[name="item_id"]');
          if (select) {
            updateItemQuantity(select, data.item_id, data.new_quantity);
          }
          if (data.guest_id && data.current_hp !== undefined) {
            const hpCell = document.querySelector(`.guest-hp[data-guest-id="${data.guest_id}"]`);
            if (hpCell) {
              hpCell.textContent = `生命 ${data.current_hp}/${data.max_hp}`;
            }
          }
          if (data.guest_id && data.status_display) {
            const statusCell = document.querySelector(`.guest-status[data-guest-id="${data.guest_id}"]`);
            if (statusCell) {
              statusCell.textContent = data.status_display;
            }
          }
          if (data.guest_id) {
            updateGuestTrainingState(data.guest_id, data.training_eta, data.training_paused);
          }
        }
      } catch (_error) {
        showError("请求失败，请重试");
      } finally {
        if (submitBtn) {
          submitBtn.disabled = false;
        }
      }
    });
  }
};

document.addEventListener("DOMContentLoaded", initGuestRosterPage);
document.addEventListener("partial-nav:loaded", initGuestRosterPage);
