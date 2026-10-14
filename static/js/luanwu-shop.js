(() => {
  "use strict";

  let choosing = false;
  document.querySelectorAll(".luanwu-purchase-form").forEach((form) => {
    const mode = form.querySelector('[name="currency_mode"]');
    const quantity = form.querySelector('[name="quantity"]');
    let submitting = false;
    let approved = false;

    // 弹框不可用时保留表单选择，默认仅使用非绑定币。
    if (typeof window.gameDialog?.confirm !== "function") return;
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
})();
