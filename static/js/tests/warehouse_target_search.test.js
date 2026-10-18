const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.resolve(__dirname, "..", "warehouse-page.js"), "utf8");

function createEventTarget(initial = {}) {
  return {
    ...initial,
    listeners: new Map(),
    addEventListener(type, listener) {
      this.listeners.set(type, listener);
    },
    dispatch(type, event = {}) {
      return this.listeners.get(type)?.(event);
    },
  };
}

function createWarehouseSearchEnv(fetch) {
  const searchInput = createEventTarget({ value: "" });
  const searchButton = createEventTarget({ disabled: false, textContent: "搜索" });
  searchButton.click = () => searchButton.dispatch("click");
  const results = createEventTarget({
    value: "",
    children: [],
    replaceChildren() {
      this.children = [];
    },
    appendChild(option) {
      this.children.push(option);
    },
  });
  const status = { textContent: "" };
  const confirmButton = { disabled: true };
  const targetForm = {
    dataset: { targetSearchUrl: "/api/map/search/" },
    querySelector(selector) {
      return {
        ".tw-target-manor-search": searchInput,
        ".js-search-target-manors": searchButton,
        ".tw-target-manor-results": results,
        "[data-target-manor-status]": status,
        "#target-manor-confirm-btn": confirmButton,
      }[selector] || null;
    },
  };
  const modal = createEventTarget({ dataset: {}, style: {} });
  modal.setAttribute = () => {};
  const listeners = new Map();
  const sessionStorage = {
    getItem() {
      return null;
    },
    setItem() {},
    removeItem() {},
  };
  const document = {
    documentElement: { scrollTop: 0 },
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
    querySelector(selector) {
      return selector === ".tw-warehouse-card" ? { dataset: {} } : null;
    },
    querySelectorAll(selector) {
      return selector === ".js-target-item-form" ? [targetForm] : [];
    },
    getElementById(id) {
      return id === "target-manor-modal" ? modal : null;
    },
    createElement() {
      return { value: "", textContent: "", disabled: false };
    },
  };
  const window = {
    WarehousePageCore: {},
    location: { origin: "https://game.example" },
    gameDialog: { error: () => Promise.resolve() },
    scrollY: 0,
    scrollTo() {},
  };
  const context = { window, document, sessionStorage, fetch, console, URL, setTimeout, clearTimeout };
  vm.runInNewContext(source, context, { filename: "warehouse-page.js" });
  listeners.get("DOMContentLoaded")();

  return { searchInput, searchButton, results, status, confirmButton };
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function searchResponse(results) {
  return { ok: true, json: async () => ({ success: true, results }) };
}

test("warehouse target search ignores an older response after a newer query finishes", async () => {
  const firstRequest = deferred();
  const secondRequest = deferred();
  const requests = [firstRequest, secondRequest];
  const env = createWarehouseSearchEnv(() => requests.shift().promise);

  env.searchInput.value = "旧查询";
  const oldSearch = env.searchButton.click();
  env.searchInput.value = "新查询";
  env.searchInput.dispatch("input");
  const newSearch = env.searchButton.click();

  secondRequest.resolve(searchResponse([
    { id: 2, name: "新查询庄园", location_display: "并州", prestige: 10, can_attack: true },
  ]));
  await newSearch;
  firstRequest.resolve(searchResponse([
    { id: 1, name: "旧查询庄园", location_display: "冀州", prestige: 20, can_attack: true },
  ]));
  await oldSearch;

  assert.equal(env.results.children.some((option) => option.textContent.includes("新查询庄园")), true);
  assert.equal(env.results.children.some((option) => option.textContent.includes("旧查询庄园")), false);
  assert.equal(env.status.textContent, "找到 1 个庄园，其中 1 个可攻击。");
});

test("warehouse target search announces when every result is attack protected", async () => {
  const env = createWarehouseSearchEnv(async () => searchResponse([
    { id: 1, name: "甲庄园", location_display: "冀州", prestige: 20, can_attack: false, attack_reason: "新手保护" },
    { id: 2, name: "乙庄园", location_display: "并州", prestige: 30, can_attack: false, attack_reason: "冷却中" },
  ]));

  env.searchInput.value = "庄园";
  await env.searchButton.click();

  assert.equal(env.status.textContent, "找到 2 个庄园，当前都受攻击保护。");
  assert.equal(env.results.children.filter((option) => option.value).every((option) => option.disabled), true);
});
