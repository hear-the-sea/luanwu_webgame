(() => {
  const page = document.querySelector("[data-research-page]");
  if (!page || page.dataset.researchBound) return;
  page.dataset.researchBound = "true";

  page.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-research-open]");
    if (trigger) {
      const dialog = document.getElementById(trigger.dataset.researchOpen);
      if (dialog && !dialog.open) dialog.showModal();
      return;
    }
    const close = event.target.closest("[data-research-close]");
    if (close) close.closest("dialog").close();
  });

  page.querySelectorAll(".research-dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      const bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right ||
          event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
    });
  });
})();
