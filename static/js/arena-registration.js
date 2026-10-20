(function () {
  const init = () => {
    const panels = Array.from(document.querySelectorAll(".tw-arena-signup-panel[data-max-selected]"));
    if (!panels.length) {
      return;
    }

    panels.forEach((panel) => {
      if (panel.dataset.registrationBound === "1") return;
      const maxSelected = Number.parseInt(panel.dataset.maxSelected || "10", 10);
      const requiredSelected = Number.parseInt(panel.dataset.requiredSelected || "0", 10);
      const checkboxes = Array.from(panel.querySelectorAll(".arena-guest-checkbox"));
      if (!checkboxes.length) {
        return;
      }
      panel.dataset.registrationBound = "1";

      checkboxes.forEach((checkbox) => {
        checkbox.addEventListener("change", async () => {
          const selected = checkboxes.filter((item) => item.checked);
          if (selected.length <= maxSelected) {
            return;
          }

          checkbox.checked = false;
          if (window.gameDialog && typeof window.gameDialog.error === "function") {
            await window.gameDialog.error(`最多只能选择 ${maxSelected} 名门客`);
            return;
          }
          window.alert(`最多只能选择 ${maxSelected} 名门客`);
        });
      });

      if (requiredSelected > 0) {
        const form = panel.querySelector("form");
        if (!form) {
          return;
        }
        form.addEventListener("submit", async (event) => {
          const selectedCount = checkboxes.filter((checkbox) => checkbox.checked).length;
          if (selectedCount === requiredSelected) {
            return;
          }
          event.preventDefault();
          const message = `请恰好选择 ${requiredSelected} 名门客（当前已选 ${selectedCount} 名）`;
          if (window.gameDialog && typeof window.gameDialog.error === "function") {
            await window.gameDialog.error(message);
            return;
          }
          window.alert(message);
        });
      }
    });
  };
  init();
  document.addEventListener("partial-nav:loaded", init);
})();
