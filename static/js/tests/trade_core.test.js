const test = require("node:test");
const assert = require("node:assert/strict");

const tradeCore = require("../trade_core.js");

test("formatMarketCountdown formats hours, minutes and expired states", () => {
  assert.equal(tradeCore.formatMarketCountdown(0), "已过期");
  assert.equal(tradeCore.formatMarketCountdown(59), "59秒");
  assert.equal(tradeCore.formatMarketCountdown(125), "2分5秒");
  assert.equal(tradeCore.formatMarketCountdown(3660), "1小时1分");
});

test("formatAuctionCountdown formats day, hour and minute buckets", () => {
  assert.equal(tradeCore.formatAuctionCountdown(0), "已结束");
  assert.equal(tradeCore.formatAuctionCountdown(59), "59秒");
  assert.equal(tradeCore.formatAuctionCountdown(125), "2分5秒");
  assert.equal(tradeCore.formatAuctionCountdown(3900), "1小时5分");
  assert.equal(tradeCore.formatAuctionCountdown(90000), "1天1小时");
});

test("buildBidModalState keeps starting price when quota is not full", () => {
  const state = tradeCore.buildBidModalState({
    slotId: "7",
    itemName: "玄铁刀",
    cutoffPrice: "10",
    winnerCount: "3",
    bidderCount: "2",
    startingPrice: "6",
    myBidAmount: "0",
  });

  assert.deepEqual(state, {
    slotId: 7,
    itemName: "玄铁刀",
    cutoffPrice: 10,
    winnerCount: 3,
    bidderCount: 2,
    startingPrice: 6,
    myBidAmount: 0,
    myBidVisible: false,
    myBidText: "0 金条",
    minBid: 6,
    hintText: "名额未满，最低 6 金条即可进入中标范围",
  });
});

test("buildBidModalState raises threshold when quota is already full", () => {
  const state = tradeCore.buildBidModalState({
    cutoffPrice: "18",
    winnerCount: "2",
    bidderCount: "2",
    startingPrice: "6",
    myBidAmount: "0",
  });

  assert.equal(state.minBid, 19);
  assert.equal(
    state.hintText,
    "名额已满，需要高于最低中标价 18 金条才能进入前 2 名"
  );
  assert.equal(state.myBidVisible, false);
});

test("buildBidModalState prefers the current bidder amount when user already bid", () => {
  const state = tradeCore.buildBidModalState({
    cutoffPrice: "18",
    winnerCount: "2",
    bidderCount: "8",
    startingPrice: "6",
    myBidAmount: "21",
  });

  assert.equal(state.minBid, 22);
  assert.equal(state.myBidVisible, true);
  assert.equal(state.myBidText, "21 金条");
  assert.equal(state.hintText, "需要高于您当前出价 21 金条");
});

test("calculateListingTotal multiplies normalized integer inputs", () => {
  assert.equal(tradeCore.calculateListingTotal("3", "20"), 60);
  assert.equal(tradeCore.calculateListingTotal("bad", "20"), 0);
  assert.equal(tradeCore.calculateListingTotal("3", null), 0);
});
