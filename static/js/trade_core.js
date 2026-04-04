(function (root, factory) {
  const api = factory();

  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }

  root.TradePageCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const DEFAULT_MARKET_DURATION = "7200";
  const ONE_HOUR_SECONDS = 3600;
  const ONE_MINUTE_SECONDS = 60;
  const ONE_DAY_SECONDS = 86400;

  function parseInteger(value, fallback = 0) {
    const parsed = Number.parseInt(String(value || ""), 10);
    return Number.isNaN(parsed) ? fallback : parsed;
  }

  function formatMarketCountdown(remainingSeconds) {
    if (remainingSeconds <= 0) {
      return "已过期";
    }
    const hours = Math.floor(remainingSeconds / ONE_HOUR_SECONDS);
    const minutes = Math.floor((remainingSeconds % ONE_HOUR_SECONDS) / ONE_MINUTE_SECONDS);
    const seconds = remainingSeconds % ONE_MINUTE_SECONDS;
    if (hours > 0) {
      return `${hours}小时${minutes}分`;
    }
    if (minutes > 0) {
      return `${minutes}分${seconds}秒`;
    }
    return `${seconds}秒`;
  }

  function formatAuctionCountdown(remainingSeconds) {
    if (remainingSeconds <= 0) {
      return "已结束";
    }
    const days = Math.floor(remainingSeconds / ONE_DAY_SECONDS);
    const hours = Math.floor((remainingSeconds % ONE_DAY_SECONDS) / ONE_HOUR_SECONDS);
    const minutes = Math.floor((remainingSeconds % ONE_HOUR_SECONDS) / ONE_MINUTE_SECONDS);
    const seconds = remainingSeconds % ONE_MINUTE_SECONDS;
    if (days > 0) {
      return `${days}天${hours}小时`;
    }
    if (hours > 0) {
      return `${hours}小时${minutes}分`;
    }
    if (minutes > 0) {
      return `${minutes}分${seconds}秒`;
    }
    return `${seconds}秒`;
  }

  function calculateListingTotal(quantity, unitPrice) {
    return parseInteger(quantity, 0) * parseInteger(unitPrice, 0);
  }

  function buildBidModalState(rawState) {
    const slotId = parseInteger(rawState && rawState.slotId, 0);
    const itemName = String((rawState && rawState.itemName) || "");
    const cutoffPrice = parseInteger(rawState && rawState.cutoffPrice, 0);
    const winnerCount = parseInteger(rawState && rawState.winnerCount, 0);
    const bidderCount = parseInteger(rawState && rawState.bidderCount, 0);
    const startingPrice = Math.max(1, parseInteger(rawState && rawState.startingPrice, 1));
    const myBidAmount = parseInteger(rawState && rawState.myBidAmount, 0);

    let minBid = startingPrice;
    let hintText = `名额未满，最低 ${startingPrice} 金条即可进入中标范围`;
    let myBidVisible = false;

    if (myBidAmount > 0) {
      myBidVisible = true;
      minBid = myBidAmount + 1;
      hintText = `需要高于您当前出价 ${myBidAmount} 金条`;
    } else if (winnerCount > 0 && bidderCount >= winnerCount) {
      minBid = cutoffPrice + 1;
      hintText = `名额已满，需要高于最低中标价 ${cutoffPrice} 金条才能进入前 ${winnerCount} 名`;
    }

    return {
      slotId,
      itemName,
      cutoffPrice,
      winnerCount,
      bidderCount,
      startingPrice,
      myBidAmount,
      myBidVisible,
      myBidText: `${myBidAmount} 金条`,
      minBid,
      hintText,
    };
  }

  return {
    DEFAULT_MARKET_DURATION,
    ONE_HOUR_SECONDS,
    ONE_MINUTE_SECONDS,
    parseInteger,
    formatMarketCountdown,
    formatAuctionCountdown,
    calculateListingTotal,
    buildBidModalState,
  };
});
