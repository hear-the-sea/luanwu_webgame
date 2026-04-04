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
  let marketCountdownTimer = null;
  let auctionCountdownTimer = null;

  function clearTimers() {
    if (marketCountdownTimer !== null) {
      window.clearInterval(marketCountdownTimer);
      marketCountdownTimer = null;
    }
    if (auctionCountdownTimer !== null) {
      window.clearInterval(auctionCountdownTimer);
      auctionCountdownTimer = null;
    }
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
    activeRoot = null;
    clickHandler = null;
    changeHandler = null;
    inputHandler = null;
    submitHandler = null;
  }

  function updateMarketCountdowns(root) {
    const countdownElements = root.querySelectorAll(".tw-market-countdown");
    if (countdownElements.length === 0) {
      clearTimers();
      return;
    }

    countdownElements.forEach((element) => {
      const expiresAt = Date.parse(element.dataset.expires || "");
      if (Number.isNaN(expiresAt)) {
        return;
      }
      const remainingSeconds = Math.max(0, Math.floor((expiresAt - Date.now()) / 1000));
      element.textContent = tradeCore.formatMarketCountdown(remainingSeconds);
      element.style.color =
        remainingSeconds <= 0
          ? "var(--text-muted)"
          : remainingSeconds < ONE_HOUR_SECONDS
            ? "#FF6B6B"
            : "var(--text-secondary)";
    });
  }

  function startMarketCountdowns(root) {
    updateMarketCountdowns(root);
    if (root.querySelector(".tw-market-countdown")) {
      marketCountdownTimer = window.setInterval(() => updateMarketCountdowns(root), 1000);
    }
  }

  function startAuctionCountdown(root) {
    const countdownElement = root.querySelector(".auction-countdown");
    if (!countdownElement) {
      return;
    }

    let remaining = tradeCore.parseInteger(countdownElement.dataset.remaining, -1);
    if (remaining < 0) {
      return;
    }

    const tick = () => {
      if (!countdownElement.isConnected) {
        if (auctionCountdownTimer !== null) {
          window.clearInterval(auctionCountdownTimer);
          auctionCountdownTimer = null;
        }
        return;
      }
      countdownElement.textContent = tradeCore.formatAuctionCountdown(remaining);
      countdownElement.style.color =
        remaining <= 0 ? "var(--text-muted)" : remaining < ONE_HOUR_SECONDS ? "#FF6B6B" : "var(--text-secondary)";
      remaining -= 1;
    };

    tick();
    auctionCountdownTimer = window.setInterval(tick, 1000);
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

  function openListingModal(root, trigger) {
    const modal = root.querySelector("#listingModal");
    const itemKeyElement = root.querySelector("#modal-item-key");
    const itemNameElement = root.querySelector("#modal-item-name");
    const availableElement = root.querySelector("#modal-available");
    const minPriceElement = root.querySelector("#modal-min-price");
    const quantityInput = root.querySelector("#modal-quantity");
    const unitPriceInput = root.querySelector("#modal-unit-price");
    const itemIconElement = root.querySelector("#modal-item-icon");
    const itemInitialElement = root.querySelector("#modal-item-initial");
    if (
      !modal ||
      !itemKeyElement ||
      !itemNameElement ||
      !availableElement ||
      !minPriceElement ||
      !quantityInput ||
      !unitPriceInput ||
      !itemIconElement ||
      !itemInitialElement
    ) {
      return;
    }

    const itemKey = trigger.dataset.itemKey || "";
    const itemName = trigger.dataset.itemName || "";
    const available = tradeCore.parseInteger(trigger.dataset.available, 0);
    const minPrice = tradeCore.parseInteger(trigger.dataset.minPrice, 0);
    const rarity = trigger.dataset.rarity || "gray";
    const imageUrl = trigger.dataset.imageUrl || "";

    itemKeyElement.value = itemKey;
    itemNameElement.textContent = itemName;
    itemNameElement.className = `tw-item-name rarity-text-${rarity}`;
    availableElement.textContent = String(available);
    minPriceElement.textContent = minPrice.toLocaleString();

    itemIconElement.className = `tw-item-icon rarity-${rarity}`;
    itemIconElement.textContent = "";
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
      itemInitialElement.textContent = placeholder.textContent;
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

      const closeButton = event.target.closest("[data-trade-close-modal]");
      if (closeButton && root.contains(closeButton)) {
        event.preventDefault();
        if (closeButton.dataset.tradeCloseModal === "bid") {
          closeBidModal(root);
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
    };

    changeHandler = (event) => {
      if (event.target.matches('input[name="duration"]')) {
        updateListingFee(root);
      }
    };

    inputHandler = (event) => {
      if (event.target.matches("#modal-quantity, #modal-unit-price")) {
        updateListingTotalPrice(root);
      }
    };

    submitHandler = async (event) => {
      const form = event.target.closest(".market-cancel-form");
      if (!form || !root.contains(form)) {
        return;
      }
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
    };

    root.addEventListener("click", clickHandler);
    root.addEventListener("change", changeHandler);
    root.addEventListener("input", inputHandler);
    root.addEventListener("submit", submitHandler);
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
    bindTradeEvents(root);
    initTooltip();
    startMarketCountdowns(root);
    startAuctionCountdown(root);
  }

  initTradePage();
  document.addEventListener("DOMContentLoaded", initTradePage);
  document.addEventListener("partial-nav:loaded", initTradePage);
})();
