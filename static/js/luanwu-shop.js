(() => {
  "use strict";

  let cleanupLayout = () => {};
  let currentGrid = null;
  const init = () => {
    const grid = document.querySelector(".luanwu-product-grid");
    if (currentGrid && currentGrid !== grid) {
      cleanupLayout();
      currentGrid = null;
    }
    const pagination = document.querySelector(".luanwu-shop__pagination");
    const products = grid ? [...grid.querySelectorAll(".luanwu-product-card")] : [];
    const previousButton = pagination?.querySelector("[data-shop-page-previous]");
    const nextButton = pagination?.querySelector("[data-shop-page-next]");
    const pageInfo = pagination?.querySelector("[data-shop-page-info]");

    if (grid && grid.dataset.paginationBound !== "1" && pagination && products.length && previousButton && nextButton && pageInfo) {
      grid.dataset.paginationBound = "1";
      currentGrid = grid;
      let pageSize = products.length;
      let currentPage = 0;
      let layoutFrame = 0;

      const renderPage = () => {
        const pageCount = Math.ceil(products.length / pageSize);
        currentPage = Math.min(Math.max(currentPage, 0), pageCount - 1);
        const firstProduct = currentPage * pageSize;
        const lastProduct = firstProduct + pageSize;

        products.forEach((product, index) => {
          product.hidden = index < firstProduct || index >= lastProduct;
        });

        pagination.hidden = pageCount <= 1;
        previousButton.disabled = currentPage === 0;
        nextButton.disabled = currentPage >= pageCount - 1;
        pageInfo.textContent = `${currentPage + 1} / ${pageCount}`;
      };

      const measurePageSize = () => {
        const previousFirstProduct = products.findIndex((product) => !product.hidden);
        const anchorIndex = Math.max(previousFirstProduct, 0);

        grid.style.visibility = "hidden";
        pagination.hidden = false;
        pagination.style.visibility = "hidden";
        products.forEach((product) => {
          product.hidden = false;
        });

        const gridStyle = window.getComputedStyle(grid);
        const columns = Math.max(1, gridStyle.gridTemplateColumns.trim().split(/\s+/).length);
        const rowGap = Number.parseFloat(gridStyle.rowGap) || 0;
        const tallestCard = Math.max(...products.map((product) => product.getBoundingClientRect().height));
        const availableHeight = grid.clientHeight;
        const rows = Math.max(
          1,
          Math.floor((availableHeight + rowGap) / Math.max(1, tallestCard + rowGap)),
        );

        pageSize = Math.max(1, columns * rows);
        currentPage = Math.floor(anchorIndex / pageSize);
        grid.style.visibility = "";
        pagination.style.visibility = "";
        renderPage();
      };

      const scheduleLayout = () => {
        if (!grid.isConnected) return;
        if (layoutFrame) window.cancelAnimationFrame(layoutFrame);
        layoutFrame = window.requestAnimationFrame(() => {
          layoutFrame = 0;
          measurePageSize();
        });
      };

      previousButton.addEventListener("click", () => {
        currentPage -= 1;
        grid.scrollTop = 0;
        renderPage();
      });
      nextButton.addEventListener("click", () => {
        currentPage += 1;
        grid.scrollTop = 0;
        renderPage();
      });
      window.addEventListener("resize", scheduleLayout, { passive: true });
      window.visualViewport?.addEventListener("resize", scheduleLayout, { passive: true });
      cleanupLayout = () => {
        if (layoutFrame) window.cancelAnimationFrame(layoutFrame);
        window.removeEventListener("resize", scheduleLayout);
        window.visualViewport?.removeEventListener("resize", scheduleLayout);
      };
      document.fonts?.ready.then(scheduleLayout);
      scheduleLayout();
    }

    let choosing = false;
    document.querySelectorAll(".luanwu-purchase-form").forEach((form) => {
      if (form.dataset.purchaseBound === "1") return;
      const mode = form.querySelector('[name="currency_mode"]');
      const quantity = form.querySelector('[name="quantity"]');
      let submitting = false;
      let approved = false;

      // 弹框不可用时保留表单选择，默认仅使用非绑定币。
      if (typeof window.gameDialog?.confirm !== "function") return;
      form.dataset.purchaseBound = "1";
      mode.hidden = true;

      form.addEventListener("submit", async (event) => {
        if (submitting) {
          event.preventDefault();
          return;
        }
        if (approved) {
          submitting = true;
          return;
        }
        if (choosing) {
          event.preventDefault();
          return;
        }
        if (Number(form.dataset.boundCoins) <= 0) {
          mode.value = "unbound_only";
          submitting = true;
          return;
        }

        event.preventDefault();
        choosing = true;
        quantity.disabled = true;
        const amount = Number(quantity.value);
        const cost = amount * Number(form.dataset.price);
        try {
          const useBound = await window.gameDialog.confirm(
            `兑换${form.dataset.productName} ×${amount}，需春秋币 ${cost} 枚。` +
              `当前绑定 ${form.dataset.boundCoins} 枚，非绑定 ${form.dataset.unboundCoins} 枚。` +
              "允许使用绑定币时优先扣除绑定币，使用绑定币兑换的该份奖励将绑定；" +
              "仅使用非绑定币时，余额不足会停止兑换。关闭此窗口可取消兑换。",
            {
              title: "选择春秋币使用方式",
              okText: "允许使用绑定币",
              cancelText: "仅使用非绑定币",
              closeOnOverlay: false,
            },
          );
          if (useBound === null || useBound === undefined) return;
          mode.value = useBound ? "allow_bound" : "unbound_only";
          quantity.disabled = false;
          approved = true;
          form.requestSubmit();
        } finally {
          quantity.disabled = false;
          choosing = false;
        }
      });
    });
  };
  init();
  document.addEventListener("partial-nav:loaded", init);
})();
