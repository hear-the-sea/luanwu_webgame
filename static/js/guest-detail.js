const initGuestDetailPage = () => {
  const detailRoot = document.querySelector(".guest-detail");
  if (!detailRoot) {
    return;
  }
  if (detailRoot.dataset.pageInitialized === "1") {
    return;
  }
  detailRoot.dataset.pageInitialized = "1";

  const attributeResponseFieldMap = {
    force: "force",
    intellect: "intellect",
    defense: "defense",
    agility: "agility",
    luck: "luck",
  };

  const parseNonNegativeInt = (value, fallback = null) => {
    const parsed = Number.parseInt(value, 10);
    if (!Number.isFinite(parsed) || parsed < 0) {
      return fallback;
    }
    return parsed;
  };

  const renderAttributeIconsHtml = (value) => {
    const numeric = parseNonNegativeInt(value, 0);
    if (!numeric) {
      return "";
    }
    const tiers = [["crown", 64], ["sun", 16], ["moon", 4], ["star", 1]];
    let remaining = numeric;
    const icons = [];
    tiers.forEach(([icon, divisor]) => {
      const count = Math.floor(remaining / divisor);
      remaining %= divisor;
      for (let i = 0; i < count; i += 1) {
        icons.push(`<span class="attr-icon attr-${icon}" aria-hidden="true"></span>`);
      }
    });
    return `<span class="attr-pack">${icons.join("")}</span>`;
  };

  const setAllocateButtonsBusy = (busy) => {
    const panel = document.getElementById("guest-attribute-panel");
    if (!panel) return;
    panel.dataset.busy = busy ? "1" : "0";
    const remaining = parseNonNegativeInt(panel.querySelector(".js-attribute-points")?.textContent, 0) || 0;
    panel.querySelectorAll(".js-allocate-points-form .add-btn").forEach((button) => {
      button.disabled = Boolean(busy) || remaining <= 0;
    });
  };

  const bindDismissForms = (root = document) => {
    root.querySelectorAll(".dismiss-form").forEach((form) => {
      if (form.dataset.confirmBound === "1") {
        return;
      }
      form.dataset.confirmBound = "1";
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const name = form.getAttribute("data-guest-name") || "该门客";
        const confirmed = await gameDialog.danger(
          `${name} 将被永久辞退，已穿戴装备会自动归还仓库。确认继续吗？`,
          { title: "辞退门客" }
        );
        if (confirmed) {
          form.submit();
        }
      });
    });
  };

  const replaceAttributePanel = (html) => {
    const currentPanel = document.getElementById("guest-attribute-panel");
    if (!currentPanel || !html) return;
    const wrapper = document.createElement("div");
    wrapper.innerHTML = html.trim();
    const nextPanel = wrapper.firstElementChild;
    if (!nextPanel) return;
    currentPanel.replaceWith(nextPanel);
    bindAllocateForms();
    bindDismissForms();
    setAllocateButtonsBusy(false);
  };

  const updateAttributePanelFast = (data, form) => {
    const panel = document.getElementById("guest-attribute-panel");
    if (!panel || !data || typeof data !== "object") {
      return false;
    }
    const nextPoints = parseNonNegativeInt(data.attribute_points, null);
    if (nextPoints === null) {
      return false;
    }

    const pointsElem = panel.querySelector(".js-attribute-points");
    if (pointsElem) {
      pointsElem.textContent = String(nextPoints);
    }

    const attribute = form?.dataset?.attribute || form?.querySelector("input[name='attribute']")?.value || "";
    const payloadField = attributeResponseFieldMap[attribute];
    const nextAttrValue = parseNonNegativeInt(payloadField ? data[payloadField] : null, null);
    if (payloadField && nextAttrValue !== null) {
      const attrElem = panel.querySelector(`.js-attribute-icons[data-attribute="${attribute}"]`);
      if (attrElem) {
        attrElem.innerHTML = renderAttributeIconsHtml(nextAttrValue);
      }
    }

    setAllocateButtonsBusy(false);
    return true;
  };

  const bindAllocateForms = (root = document) => {
    root.querySelectorAll(".js-allocate-points-form").forEach((form) => {
      if (form.dataset.ajaxBound === "1") {
        return;
      }
      form.dataset.ajaxBound = "1";
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (form.dataset.submitting === "1") {
          return;
        }
        const panel = document.getElementById("guest-attribute-panel");
        if (panel?.dataset.busy === "1") {
          return;
        }
        form.dataset.submitting = "1";
        setAllocateButtonsBusy(true);
        const controller = new AbortController();
        const timeoutId = window.setTimeout(() => controller.abort(), 12000);
        try {
          const response = await fetch(form.action, {
            method: "POST",
            body: new FormData(form),
            credentials: "same-origin",
            signal: controller.signal,
            headers: {
              "X-Requested-With": "XMLHttpRequest",
              "Accept": "application/json"
            }
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok || !data.success) {
            throw new Error(data.message || data.error || "属性加点失败");
          }
          const updated = updateAttributePanelFast(data, form);
          if (!updated) {
            replaceAttributePanel(data.attribute_panel_html || "");
          }
        } catch (error) {
          const message =
            error?.name === "AbortError" ? "请求超时，请检查网络后重试" : error?.message || "请求失败，请重试";
          if (window.gameDialog?.error) {
            window.gameDialog.error(message, { title: "加点失败" });
          } else {
            alert(message);
          }
        } finally {
          window.clearTimeout(timeoutId);
          form.dataset.submitting = "0";
          setAllocateButtonsBusy(false);
        }
      });
    });
  };

  bindAllocateForms();
  bindDismissForms();

  if (typeof window.initItemTooltip === "function") {
    window.initItemTooltip({
      key: "guest_detail_equipment",
      cellSelector: ".guest-equip-tooltip-trigger",
      tooltipSelector: ".guest-equip-tooltip-bubble",
      trackPointer: false,
    });
  }

  const skillModal = document.getElementById("skill-book-modal");
  const openSkillButtons = document.querySelectorAll(".open-skill-modal");
  const closeSkillButtons = document.querySelectorAll(".close-skill-modal");
  const toggleSkillModal = (show) => {
    if (!skillModal) return;
    skillModal.style.display = show ? "flex" : "none";
  };
  openSkillButtons.forEach((btn) => btn.addEventListener("click", () => toggleSkillModal(true)));
  closeSkillButtons.forEach((btn) => btn.addEventListener("click", () => toggleSkillModal(false)));
  skillModal?.addEventListener("click", (event) => {
    if (event.target === skillModal) {
      toggleSkillModal(false);
    }
  });

  const equipModal = document.getElementById("equip-modal");
  const equipSelect = equipModal?.querySelector("select[name='gear']");
  const equipSlotLabel = equipModal?.querySelector("[data-slot-label]");
  const equipForm = equipModal?.querySelector("form");
  const openEquipButtons = document.querySelectorAll(".equip-open");
  const equipSubmit = equipForm?.querySelector("button[type='submit']");
  const gearOptionsUrl = equipModal?.getAttribute("data-options-url");
  const guestId = equipModal?.getAttribute("data-guest-id");

  const loadEquipOptions = async (slot) => {
    if (!gearOptionsUrl) return { options: [], slot_label: "" };
    const url = new URL(gearOptionsUrl, window.location.origin);
    url.searchParams.set("slot", slot);
    if (guestId) {
      url.searchParams.set("guest", guestId);
    }
    const response = await fetch(url.toString(), { credentials: "same-origin" });
    if (!response.ok) {
      throw new Error("load_failed");
    }
    return response.json();
  };

  const renderEquipOptions = (payload) => {
    if (!equipSelect) return;
    const options = payload?.options || [];
    equipSelect.innerHTML = "";
    if (!options.length) {
      equipSelect.innerHTML = '<option value="">暂无可用装备</option>';
    } else {
      options.forEach((entry) => {
        const option = document.createElement("option");
        option.value = entry.id;
        option.textContent = `[${entry.rarity_label}] ${entry.name}（x${entry.count}）`;
        option.className = `rarity-text ${entry.rarity_class || ""}`.trim();
        if (entry.title) {
          option.title = entry.title;
        }
        equipSelect.appendChild(option);
      });
    }
    const hasOptions = options.length > 0;
    equipSelect.disabled = !hasOptions;
    if (equipSubmit) {
      equipSubmit.disabled = !hasOptions;
    }
    if (equipSlotLabel) {
      equipSlotLabel.textContent = payload?.slot_label || "";
    }
  };

  const openEquipModal = async (slot) => {
    if (!equipModal || !equipSelect || !equipForm) return;
    equipSelect.innerHTML = '<option value="">加载中...</option>';
    equipSelect.disabled = true;
    if (equipSubmit) {
      equipSubmit.disabled = true;
    }
    equipForm.querySelector("input[name='slot']").value = slot || "";
    equipModal.style.display = "flex";
    if (equipSlotLabel) {
      equipSlotLabel.textContent = slot || "";
    }
    try {
      const payload = await loadEquipOptions(slot);
      renderEquipOptions(payload);
    } catch (_error) {
      equipSelect.innerHTML = '<option value="">加载失败</option>';
      equipSelect.disabled = true;
      if (equipSubmit) {
        equipSubmit.disabled = true;
      }
    }
  };

  const closeEquipModal = () => {
    if (!equipModal) return;
    equipModal.style.display = "none";
  };

  openEquipButtons.forEach((btn) =>
    btn.addEventListener("click", () => {
      const slot = btn.getAttribute("data-slot");
      openEquipModal(slot);
    })
  );

  if (equipForm) {
    equipForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formData = new FormData(equipForm);
      const submitBtn = equipForm.querySelector("button[type='submit']");
      if (submitBtn) {
        submitBtn.disabled = true;
      }

      try {
        const response = await fetch(equipForm.action, {
          method: "POST",
          body: formData,
          headers: { "X-Requested-With": "XMLHttpRequest" }
        });
        const data = await response.json();
        if (data.success) {
          closeEquipModal();
          location.reload();
        } else {
          alert(data.message || "装备失败");
        }
      } catch (_error) {
        alert("请求失败，请重试");
      } finally {
        if (submitBtn) {
          submitBtn.disabled = false;
        }
      }
    });
  }

  equipModal?.addEventListener("click", (event) => {
    if (event.target === equipModal) {
      closeEquipModal();
    }
  });
  equipModal?.querySelector(".close-equip-modal")?.addEventListener("click", closeEquipModal);
};

document.addEventListener("DOMContentLoaded", initGuestDetailPage);
document.addEventListener("partial-nav:loaded", initGuestDetailPage);
