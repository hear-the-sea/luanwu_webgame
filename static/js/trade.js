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
    activeRoot = null;
    clickHandler = null;
    changeHandler = null;
    inputHandler = null;
    submitHandler = null;
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
