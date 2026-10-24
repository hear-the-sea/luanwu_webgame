(function () {
  "use strict";

  const TRADE_PAGE_SELECTOR = '[data-trade-page="1"]';
  const tradeCore = window.TradePageCore;
  const DEFAULT_MARKET_DURATION = tradeCore.DEFAULT_MARKET_DURATION;
  const ONE_HOUR_SECONDS = tradeCore.ONE_HOUR_SECONDS;

  let activeRoot = null;
  let clickHandler = null;
  let changeHandler = null;
  let inputHandler = null;
  let submitHandler = null;
  let keydownHandler = null;
  let purchaseTrigger = null;
  let countdownTimer = null;
  let marketCountdownRoot = null;
  let marketCountdownElements = [];
  let auctionCountdownElement = null;
  let auctionRemaining = -1;
  const marketCountdownState = new WeakMap();

  function stopCountdownTimerIfIdle() {
    if (marketCountdownRoot || auctionCountdownElement) {
      return;
    }
    if (countdownTimer !== null) {
      window.clearInterval(countdownTimer);
      countdownTimer = null;
    }
  }

  function clearTimers() {
    marketCountdownRoot = null;
    marketCountdownElements = [];
    auctionCountdownElement = null;
    auctionRemaining = -1;
    stopCountdownTimerIfIdle();
  }

  function teardownTradePage() {
    clearTimers();
    if (!activeRoot) {
      return;
    }
    if (clickHandler) {
      activeRoot.removeEventListener("click", clickHandler);
    }
    if (changeHandler) {
      activeRoot.removeEventListener("change", changeHandler);
    }
    if (inputHandler) {
      activeRoot.removeEventListener("input", inputHandler);
    }
    if (submitHandler) {
      activeRoot.removeEventListener("submit", submitHandler);
    }
    if (keydownHandler) {
      activeRoot.removeEventListener("keydown", keydownHandler);
    }
    activeRoot = null;
    clickHandler = null;
    changeHandler = null;
    inputHandler = null;
    submitHandler = null;
    keydownHandler = null;
    purchaseTrigger = null;
  }

  function resetBankTransferState(root) {
    if (!root) return;
    delete root.dataset.bankTransferPending;
    root.querySelectorAll('[data-bank-transfer-pending="1"]').forEach((button) => {
      button.disabled = false;
      button.textContent = button.dataset.bankIdleLabel;
      delete button.dataset.bankTransferPending;
      delete button.dataset.bankIdleLabel;
    });
    root.querySelectorAll("[data-bank-transfer]").forEach((form) => {
      form.removeAttribute("aria-busy");
    });
  }

  function updateMarketCountdowns() {
    const countdownElements = marketCountdownElements;
    if (countdownElements.length === 0) {
      marketCountdownRoot = null;
      stopCountdownTimerIfIdle();
      return;
    }

    marketCountdownElements = countdownElements.filter((element) => {
      if (typeof element.isConnected === "boolean" && !element.isConnected) {
        return false;
      }
      return true;
    });

    marketCountdownElements.forEach((element) => {
      const expiresValue = element.dataset.expires || "";
      let state = marketCountdownState.get(element);
      if (!state || state.expiresValue !== expiresValue) {
        state = {
          expiresValue,
          expiresAt: Date.parse(expiresValue),
          lastText: null,
          lastColor: null,
        };
        marketCountdownState.set(element, state);
      }
      if (Number.isNaN(state.expiresAt)) {
        return;
      }
      const remainingSeconds = Math.max(0, Math.floor((state.expiresAt - Date.now()) / 1000));
      const countdownText = tradeCore.formatMarketCountdown(remainingSeconds);
      if (state.lastText !== countdownText || element.textContent !== countdownText) {
        element.textContent = countdownText;
        state.lastText = countdownText;
      }
      const color =
        remainingSeconds <= 0
          ? "var(--text-muted)"
          : remainingSeconds < ONE_HOUR_SECONDS
            ? "#FF6B6B"
            : "var(--text-secondary)";
      if (state.lastColor !== color) {
        element.style.color = color;
        state.lastColor = color;
      }
    });
    if (marketCountdownElements.length === 0) {
      marketCountdownRoot = null;
      stopCountdownTimerIfIdle();
    }
  }

  function updateAuctionCountdown() {
    const countdownElement = auctionCountdownElement;
    if (!countdownElement || !countdownElement.isConnected) {
      auctionCountdownElement = null;
      auctionRemaining = -1;
      stopCountdownTimerIfIdle();
      return;
    }

    countdownElement.textContent = tradeCore.formatAuctionCountdown(auctionRemaining);
    countdownElement.style.color =
      auctionRemaining <= 0
        ? "var(--text-muted)"
        : auctionRemaining < ONE_HOUR_SECONDS
          ? "#FF6B6B"
          : "var(--text-secondary)";
    auctionRemaining -= 1;
  }

  function tickCountdowns() {
    if (marketCountdownRoot) {
      updateMarketCountdowns();
    }
    if (auctionCountdownElement) {
      updateAuctionCountdown();
    }
  }

  function ensureCountdownTimer() {
    if (countdownTimer === null) {
      countdownTimer = window.setInterval(tickCountdowns, 1000);
    }
  }

  function startMarketCountdowns(root) {
    marketCountdownRoot = root;
    marketCountdownElements = Array.from(root.querySelectorAll(".tw-market-countdown"));
    updateMarketCountdowns();
    if (marketCountdownRoot) {
      ensureCountdownTimer();
    }
  }

  function startAuctionCountdown(root) {
    const countdownElement = root.querySelector(".auction-countdown");
    if (!countdownElement) {
      auctionCountdownElement = null;
      auctionRemaining = -1;
      stopCountdownTimerIfIdle();
      return;
    }

    let remaining = tradeCore.parseInteger(countdownElement.dataset.remaining, -1);
    if (remaining < 0) {
      auctionCountdownElement = null;
      auctionRemaining = -1;
      stopCountdownTimerIfIdle();
      return;
    }

    auctionCountdownElement = countdownElement;
    auctionRemaining = remaining;
    updateAuctionCountdown();
    if (auctionCountdownElement) {
      ensureCountdownTimer();
    }
  }

  function syncShopMode(root, mode) {
    const buySection = root.querySelector("#shop-buy-section");
    const sellSection = root.querySelector("#shop-sell-section");
    const modeButtons = root.querySelectorAll(".tw-mode-btn[data-mode]");
    if (!buySection || !sellSection || modeButtons.length === 0) {
      return;
    }

    modeButtons.forEach((button) => {
      button.classList.toggle("active", button.dataset.mode === mode);
    });
    buySection.classList.toggle("tw-shop-section-hidden", mode !== "buy");
    sellSection.classList.toggle("tw-shop-section-hidden", mode === "buy");

    const url = new URL(window.location.href);
    url.searchParams.set("tab", "shop");
    url.searchParams.set("view", mode);
    window.history.replaceState({}, "", url);
  }

  function buildAuctionBidAction(root, slotId) {
    const template = root.dataset.auctionBidUrlTemplate || "";
    return template.replace("/0/", `/${slotId}/`);
  }

  function openBidModal(root, trigger) {
    const modal = root.querySelector("#bidModal");
    const form = root.querySelector("#bidForm");
    const itemNameElement = root.querySelector("#bid-modal-item-name");
    const winnerCountElement = root.querySelector("#bid-modal-winner-count");
    const cutoffPriceElement = root.querySelector("#bid-modal-cutoff-price");
    const myBidGroup = root.querySelector("#bid-modal-my-bid-group");
    const myBidElement = root.querySelector("#bid-modal-my-bid");
    const hintElement = root.querySelector("#bid-modal-hint");
    const amountInput = root.querySelector("#bid-modal-amount");
    if (
      !modal ||
      !form ||
      !itemNameElement ||
      !winnerCountElement ||
      !cutoffPriceElement ||
      !myBidGroup ||
      !myBidElement ||
      !hintElement ||
      !amountInput
    ) {
      return;
    }

    const state = tradeCore.buildBidModalState({
      slotId: trigger.dataset.slotId,
      itemName: trigger.dataset.itemName,
      cutoffPrice: trigger.dataset.cutoffPrice,
      winnerCount: trigger.dataset.winnerCount,
      bidderCount: trigger.dataset.bidderCount,
      startingPrice: trigger.dataset.startingPrice,
      minIncrement: trigger.dataset.minIncrement,
      myBidAmount: trigger.dataset.myBidAmount,
    });

    form.action = buildAuctionBidAction(root, state.slotId);
    itemNameElement.textContent = state.itemName;
    winnerCountElement.textContent = `${state.winnerCount} 人`;
    cutoffPriceElement.textContent = `${state.cutoffPrice} 金条`;

    if (state.myBidVisible) {
      myBidGroup.style.display = "block";
      myBidElement.textContent = state.myBidText;
    } else {
      myBidGroup.style.display = "none";
    }

    hintElement.textContent = state.hintText;
    amountInput.value = String(state.minBid);
    amountInput.min = String(state.minBid);
    modal.style.display = "flex";
  }

  function closeBidModal(root) {
    const modal = root.querySelector("#bidModal");
    if (modal) {
      modal.style.display = "none";
    }
  }

  function updateListingFee(root) {
    const selectedDuration = root.querySelector('input[name="duration"]:checked');
    const feeElement = root.querySelector("#modal-fee");
    if (!selectedDuration || !feeElement) {
      return;
    }
    const fee = tradeCore.parseInteger(selectedDuration.dataset.tradeDurationFee, 0);
    feeElement.textContent = fee.toLocaleString();
  }

  function updateListingTotalPrice(root) {
    const quantityInput = root.querySelector("#modal-quantity");
    const unitPriceInput = root.querySelector("#modal-unit-price");
    const totalPriceElement = root.querySelector("#modal-total-price");
    if (!quantityInput || !unitPriceInput || !totalPriceElement) {
      return;
    }
    totalPriceElement.textContent = tradeCore.calculateListingTotal(
      quantityInput.value,
      unitPriceInput.value
    ).toLocaleString();
  }

  function updateMarketPurchaseTotal(form) {
    const input = form.querySelector('input[name="quantity"]');
    const total = form.querySelector("[data-market-purchase-total]");
    const confirm = form.querySelector("[data-market-purchase-confirm]");
    const hint = form.querySelector("#market-purchase-hint");
    if (!total || !confirm || !hint) return;
    const available = Number(form.dataset.available);
    const quantity = input ? Number(input.value) : available;
    const price = Number(form.dataset.unitPrice);
    const valid = Number.isSafeInteger(quantity) && quantity > 0 && quantity <= available
      && Number.isSafeInteger(price) && price > 0;
    const amount = valid ? BigInt(quantity) * BigInt(price) : 0n;
    const affordable = valid && amount <= BigInt(form.dataset.silver || "0");
    total.textContent = valid ? amount.toLocaleString() : "—";
    confirm.disabled = !affordable || form.dataset.purchasePending === "1";
    hint.textContent = !valid
      ? `请输入 1 至 ${available.toLocaleString()} 的整数数量。`
      : !affordable ? "银两不足，请减少购买数量。" : `最多可购买 ${available.toLocaleString()} 件。`;
  }

  function openMarketPurchaseModal(root, trigger) {
    const modal = root.querySelector("#marketPurchaseModal");
    const form = modal?.querySelector("[data-market-purchase]");
    if (!form || trigger.disabled || form.dataset.purchasePending === "1") return;
    const available = Number(trigger.dataset.available);
    const price = Number(trigger.dataset.unitPrice);
    if (!trigger.dataset.purchaseUrl || !Number.isSafeInteger(available) || available <= 0
      || !Number.isSafeInteger(price) || price <= 0) return;
    form.action = trigger.dataset.purchaseUrl;
    form.dataset.available = String(available);
    form.dataset.unitPrice = String(price);
    const name = modal.querySelector("#market-purchase-item-name");
    name.textContent = trigger.dataset.itemName || "物品";
    name.className = `tw-item-name rarity-${trigger.dataset.rarity || "gray"}`;
    modal.querySelector("[data-purchase-available]").textContent = available.toLocaleString();
    modal.querySelector("[data-purchase-unit-price]").textContent = price.toLocaleString();
    const input = form.querySelector('input[name="quantity"]');
    form.querySelector('input[name="purchase_mode"]').value = input ? "partial" : "all";
    if (input) {
      input.max = String(available);
      input.value = "1";
    }
    purchaseTrigger = trigger;
    updateMarketPurchaseTotal(form);
    modal.style.display = "flex";
    (input || modal.querySelector("[data-market-purchase-confirm]")).focus();
    input?.select();
  }

  function closeMarketPurchaseModal(root) {
    const modal = root.querySelector("#marketPurchaseModal");
    if (modal) modal.style.display = "none";
    if (purchaseTrigger?.isConnected) purchaseTrigger.focus();
    purchaseTrigger = null;
  }

  function resetMarketPurchaseState(root) {
    const form = root?.querySelector("[data-market-purchase]");
    if (!form) return;
    delete form.dataset.purchasePending;
    form.removeAttribute("aria-busy");
    form.querySelector("[data-market-purchase-confirm]").textContent = "确认";
    if (form.dataset.available) updateMarketPurchaseTotal(form);
  }

  function openListingModal(root, trigger) {
    const modal = root.querySelector("#listingModal");
    const itemKeyElement = root.querySelector("#modal-item-key");
    const itemNameElement = root.querySelector("#modal-item-name");
    const availableElement = root.querySelector("#modal-available");
    const minPriceElement = root.querySelector("#modal-min-price");
    const quantityInput = root.querySelector("#modal-quantity");
    const unitPriceInput = root.querySelector("#modal-unit-price");
    const itemIconElement = root.querySelector("#modal-item-icon");
    if (
      !modal ||
      !itemKeyElement ||
      !itemNameElement ||
      !availableElement ||
      !minPriceElement ||
      !quantityInput ||
      !unitPriceInput ||
      !itemIconElement
    ) {
      return;
    }

    const itemKey = trigger.dataset.itemKey || "";
    const itemName = trigger.dataset.itemName || "";
    const available = tradeCore.parseInteger(trigger.dataset.available, 0);
    const referencePrice = Math.max(0, tradeCore.parseInteger(trigger.dataset.minPrice, 0));
    const minPrice = Math.max(1, referencePrice);
    const rarity = trigger.dataset.rarity || "gray";
    const imageUrl = trigger.dataset.imageUrl || "";

    itemKeyElement.value = itemKey;
    itemNameElement.textContent = itemName;
    itemNameElement.className = `tw-item-name rarity-text-${rarity}`;
    availableElement.textContent = String(available);
    minPriceElement.textContent = referencePrice.toLocaleString();

    itemIconElement.className = `tw-item-icon rarity-${rarity}`;
    itemIconElement.replaceChildren();
    if (imageUrl) {
      const img = document.createElement("img");
      img.src = imageUrl;
      img.alt = itemName;
      img.loading = "lazy";
      img.decoding = "async";
      img.style.width = "100%";
      img.style.height = "100%";
      img.style.objectFit = "contain";
      itemIconElement.appendChild(img);
    } else {
      const placeholder = document.createElement("span");
      placeholder.className = "tw-icon-placeholder";
      placeholder.textContent = itemName ? itemName.charAt(0) : "?";
      itemIconElement.appendChild(placeholder);
    }

    quantityInput.value = "1";
    quantityInput.max = String(available);
    unitPriceInput.value = String(minPrice);
    unitPriceInput.min = String(minPrice);

    const defaultDuration = root.dataset.defaultMarketDuration || DEFAULT_MARKET_DURATION;
    const durationRadios = Array.from(root.querySelectorAll('input[name="duration"]'));
    let matchedDefaultDuration = false;
    durationRadios.forEach((radio) => {
      const isDefault = radio.value === defaultDuration;
      radio.checked = isDefault;
      matchedDefaultDuration = matchedDefaultDuration || isDefault;
    });
    if (!matchedDefaultDuration && durationRadios.length > 0) {
      durationRadios[0].checked = true;
    }
    updateListingFee(root);
    updateListingTotalPrice(root);
    modal.style.display = "flex";
  }

  function closeListingModal(root) {
    const modal = root.querySelector("#listingModal");
    if (modal) {
      modal.style.display = "none";
    }
  }

  function submitTradeFilter(control) {
    const form =
      control.form
      || (typeof control.closest === "function" ? control.closest("form") : null);
    if (!form) {
      return;
    }
    if (typeof form.requestSubmit === "function") {
      form.requestSubmit();
      return;
    }
    if (typeof form.submit === "function") {
      form.submit();
    }
  }

  function confirmShopSellAll(itemName, quantity) {
    const message = `确认全部卖出「${itemName}」×${quantity}？卖出后无法撤回。`;
    const options = {
      title: "全部卖出确认",
      okText: "确认卖出",
      cancelText: "保留物品",
      danger: true,
    };
    if (typeof window.gameDialog?.danger === "function") {
      return window.gameDialog.danger(message, options);
    }
    if (typeof window.gameConfirm === "function") {
      return window.gameConfirm(message, options);
    }
    return Promise.resolve(window.confirm(message));
  }

  function bindTradeEvents(root) {
    clickHandler = (event) => {
      const shopModeButton = event.target.closest(".tw-mode-btn[data-mode]");
      if (shopModeButton && root.contains(shopModeButton)) {
        event.preventDefault();
        syncShopMode(root, shopModeButton.dataset.mode || "buy");
        return;
      }

      const bidButton = event.target.closest(".js-open-bid-modal");
      if (bidButton && root.contains(bidButton)) {
        event.preventDefault();
        openBidModal(root, bidButton);
        return;
      }

      const listingButton = event.target.closest(".js-open-listing-modal");
      if (listingButton && root.contains(listingButton)) {
        event.preventDefault();
        openListingModal(root, listingButton);
        return;
      }

      const purchaseButton = event.target.closest(".js-open-market-purchase");
      if (purchaseButton && root.contains(purchaseButton)) {
        event.preventDefault();
        openMarketPurchaseModal(root, purchaseButton);
        return;
      }

      const purchaseAll = event.target.closest("[data-market-purchase-all]");
      if (purchaseAll && root.contains(purchaseAll)) {
        const form = purchaseAll.closest("[data-market-purchase]");
        if (form.dataset.purchasePending === "1") return;
        form.querySelector('input[name="quantity"]').value = form.dataset.available;
        form.querySelector('input[name="purchase_mode"]').value = "all";
        updateMarketPurchaseTotal(form);
        return;
      }

      const closeButton = event.target.closest("[data-trade-close-modal]");
      if (closeButton && root.contains(closeButton)) {
        event.preventDefault();
        if (closeButton.dataset.tradeCloseModal === "bid") {
          closeBidModal(root);
        } else if (closeButton.dataset.tradeCloseModal === "purchase") {
          closeMarketPurchaseModal(root);
        } else {
          closeListingModal(root);
        }
        return;
      }

      if (event.target.id === "bidModal") {
        closeBidModal(root);
        return;
      }

      if (event.target.id === "listingModal") {
        closeListingModal(root);
      }
      if (event.target.id === "marketPurchaseModal") {
        closeMarketPurchaseModal(root);
      }
    };

    changeHandler = (event) => {
      if (event.target.matches && event.target.matches('input[name="duration"]')) {
        updateListingFee(root);
      }
      if (
        event.target.matches
        && event.target.matches("[data-trade-auto-submit]")
        && root.contains(event.target)
      ) {
        submitTradeFilter(event.target);
      }
    };

    inputHandler = (event) => {
      const purchaseForm = event.target.closest("[data-market-purchase]");
      if (purchaseForm && root.contains(purchaseForm)) {
        purchaseForm.querySelector('input[name="purchase_mode"]').value = "partial";
        updateMarketPurchaseTotal(purchaseForm);
      }
      if (event.target.matches("#modal-quantity, #modal-unit-price")) {
        updateListingTotalPrice(root);
      }
    };

    submitHandler = async (event) => {
      const purchaseForm = event.target.closest("[data-market-purchase]");
      if (purchaseForm && root.contains(purchaseForm)) {
        updateMarketPurchaseTotal(purchaseForm);
        const confirm = purchaseForm.querySelector("[data-market-purchase-confirm]");
        if (confirm.disabled || !purchaseForm.checkValidity()) {
          event.preventDefault();
          purchaseForm.reportValidity();
          return;
        }
        purchaseForm.dataset.purchasePending = "1";
        purchaseForm.setAttribute("aria-busy", "true");
        confirm.disabled = true;
        confirm.textContent = "购买中…";
        return;
      }
      const bankForm = event.target.closest("[data-bank-transfer]");
      if (bankForm && root.contains(bankForm)) {
        if (root.dataset.bankTransferPending === "1") {
          event.preventDefault();
          return;
        }
        root.dataset.bankTransferPending = "1";
        bankForm.setAttribute("aria-busy", "true");
        root.querySelectorAll('[data-bank-transfer] button[type="submit"]').forEach((button) => {
          if (!button.disabled) {
            button.dataset.bankTransferPending = "1";
            button.dataset.bankIdleLabel = button.textContent;
            button.disabled = true;
          }
        });
        if (event.submitter) event.submitter.textContent = "处理中…";
        return;
      }
      const form = event.target.closest(".market-cancel-form");
      if (form && root.contains(form)) {
        event.preventDefault();
        try {
          const confirmed = await window.gameConfirm("确定取消上架吗？物品将退回仓库，但手续费不退还。", {
            title: "取消上架",
          });
          if (confirmed) {
            form.submit();
          }
        } catch (error) {
          console.error("取消上架确认失败:", error);
        }
        return;
      }

      const sellAllButton = event.submitter?.closest?.("[data-shop-sell-all]");
      const sellForm = sellAllButton?.closest(".shop-sell-form");
      if (!sellAllButton || !sellForm || !root.contains(sellForm) || sellForm.dataset.confirmPending === "1") {
        return;
      }

      event.preventDefault();
      const quantityInput = sellForm.querySelector('input[name="quantity"]');
      const quantity = tradeCore.parseInteger(sellAllButton.dataset.itemQuantity, 0);
      if (!quantityInput || quantity <= 0) {
        return;
      }

      quantityInput.value = String(quantity);
      sellForm.dataset.confirmPending = "1";
      sellAllButton.disabled = true;
      let submitted = false;
      try {
        const confirmed = await confirmShopSellAll(
          sellAllButton.dataset.itemName || "该道具",
          quantity,
        );
        if (confirmed) {
          sellForm.submit();
          submitted = true;
          return;
        }
      } catch (error) {
        console.error("全部卖出确认失败:", error);
      } finally {
        if (!submitted && sellForm.isConnected) {
          delete sellForm.dataset.confirmPending;
          sellAllButton.disabled = false;
        }
      }
    };

    keydownHandler = (event) => {
      const modal = root.querySelector("#marketPurchaseModal");
      if (!modal || modal.style.display === "none") return;
      if (event.key === "Escape") {
        event.preventDefault();
        closeMarketPurchaseModal(root);
      } else if (event.key === "Tab") {
        const controls = Array.from(modal.querySelectorAll('button:not([disabled]), input:not([type="hidden"]):not([disabled])'));
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };

    root.addEventListener("click", clickHandler);
    root.addEventListener("change", changeHandler);
    root.addEventListener("input", inputHandler);
    root.addEventListener("submit", submitHandler);
    root.addEventListener("keydown", keydownHandler);
  }

  function initTooltip() {
    if (typeof window.initItemTooltip === "function") {
      window.initItemTooltip({ key: "trade_market", trackPointer: false });
    }
  }

  function initTradePage() {
    const root = document.querySelector(TRADE_PAGE_SELECTOR);
    if (!root) {
      teardownTradePage();
      return;
    }
    if (root === activeRoot) {
      return;
    }

    teardownTradePage();
    activeRoot = root;
    resetBankTransferState(root);
    resetMarketPurchaseState(root);
    bindTradeEvents(root);
    initTooltip();
    startMarketCountdowns(root);
    startAuctionCountdown(root);
  }

  initTradePage();
  document.addEventListener("DOMContentLoaded", initTradePage);
  document.addEventListener("partial-nav:loaded", initTradePage);
  window.addEventListener?.("pageshow", (event) => {
    if (event.persisted) {
      resetBankTransferState(activeRoot);
      resetMarketPurchaseState(activeRoot);
    }
  });
})();
