(function () {
  const init = () => {
    if (typeof window.initItemTooltip === "function") {
      window.initItemTooltip({
        key: "forge-production-product-details",
        cellSelector: ".forge-product-cell",
        tooltipSelector: ".forge-item-tooltip",
        trackPointer: false,
        enableHover: true,
        enableTouch: true,
      });
    }

    document.querySelectorAll(".js-decompose-form").forEach((form) => {
      if (form.dataset.decomposeBound === "1") return;
      form.dataset.decomposeBound = "1";
      form.addEventListener("submit", async (event) => {
        event.preventDefault();

        const confirmed =
          window.gameDialog && typeof window.gameDialog.danger === "function"
            ? await window.gameDialog.danger("确认要分解该装备吗？分解后无法恢复。", {
                title: "分解确认",
                okText: "确认分解",
                cancelText: "取消",
              })
            : window.confirm("确认要分解该装备吗？分解后无法恢复。");

        if (confirmed) {
          form.submit();
        }
      });
    });
  };
  init();
  document.addEventListener("partial-nav:loaded", init);
})();
