(function () {
  function updateTotalCost(input) {
    const targetId = input.dataset.totalTarget || "";
    const totalSpan = targetId ? document.getElementById(targetId) : null;
    const unitCost = Number.parseInt(input.dataset.unitCost || "", 10);
    const quantity = Number.parseInt(input.value || "", 10) || 1;
    const unitLabel = input.dataset.unitLabel || "";
    if (totalSpan) {
      const baseTotal = (Number.isFinite(unitCost) ? unitCost : 0) * quantity;
      const discountPercent = Number.parseInt(input.dataset.costDiscountPercent || "", 10) || 0;
      const remainingPercent = Math.max(0, 100 - discountPercent);
      const totalCost = baseTotal > 0 && discountPercent > 0
        ? Math.max(1, Math.floor((baseTotal * remainingPercent + 99) / 100))
        : baseTotal;
      totalSpan.textContent = `总计：${totalCost.toLocaleString()} ${unitLabel}`.trim();
    }

    const durationTargetId = input.dataset.durationTarget || "";
    const durationSpan = durationTargetId ? document.getElementById(durationTargetId) : null;
    const unitDuration = Number.parseInt(input.dataset.unitDuration || "", 10);
    if (durationSpan && Number.isFinite(unitDuration)) {
      durationSpan.textContent = `${(unitDuration * quantity).toLocaleString()} 秒`;
    }
  }

  document.querySelectorAll(".tw-quantity-input[data-total-target], .tw-quantity-input[data-duration-target]").forEach((input) => {
    input.addEventListener("input", () => updateTotalCost(input));
    input.addEventListener("change", () => updateTotalCost(input));
  });
})();
