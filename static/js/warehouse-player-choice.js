(() => {
  let activeModal = null;
  let lastTrigger = null;
  let previousOverflow = "";

  const close = () => {
    if (!activeModal) return;
    activeModal.style.display = "none";
    activeModal.setAttribute("aria-hidden", "true");
    document.body.style.overflow = previousOverflow;
    activeModal = null;
    lastTrigger?.focus();
    lastTrigger = null;
  };

  const updateSelection = (modal) => {
    const form = modal.querySelector("form");
    const selected = form.querySelector('input[name="guest_template_key"]:checked:not(:disabled)');
    form.querySelector('button[type="submit"]').disabled = form.dataset.hasStock !== "true" || !selected;
    form.querySelectorAll(".tw-player-choice-option").forEach((option) => {
      option.classList.toggle("selected", Boolean(option.querySelector("input:checked")));
    });
  };

  const open = (trigger) => {
    const modal = document.getElementById(trigger.dataset.playerChoiceModal);
    if (!modal) return;
    if (activeModal) close();
    modal.querySelector("form").reset();
    updateSelection(modal);
    lastTrigger = trigger;
    previousOverflow = document.body.style.overflow;
    activeModal = modal;
    modal.style.display = "flex";
    modal.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    modal.querySelector(".js-close-player-choice-modal").focus();
  };

  const onKeydown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab" || !activeModal) return;
    const focusable = [...activeModal.querySelectorAll('button:not(:disabled), input:not(:disabled)')]
      .filter((element) => element.getClientRects().length > 0);
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const init = () => {
    if (activeModal && !activeModal.isConnected) close();
    document.querySelectorAll(".js-open-player-choice-modal").forEach((trigger) => {
      if (trigger.dataset.choiceBound === "1") return;
      trigger.dataset.choiceBound = "1";
      trigger.addEventListener("click", () => open(trigger));
    });
    document.querySelectorAll(".js-player-choice-modal").forEach((modal) => {
      if (modal.dataset.choiceBound === "1") return;
      modal.dataset.choiceBound = "1";
      modal.addEventListener("change", () => updateSelection(modal));
      modal.addEventListener("keydown", onKeydown);
      modal.addEventListener("click", (event) => {
        if (event.target === modal || event.target.closest(".js-close-player-choice-modal")) close();
      });
    });
  };

  document.addEventListener("DOMContentLoaded", init);
  document.addEventListener("partial-nav:loaded", init);
})();
