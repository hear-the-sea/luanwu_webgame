const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function createClassList() {
  const classes = new Set();
  return {
    toggle(name, force) {
      if (force) {
        classes.add(name);
      } else {
        classes.delete(name);
      }
    },
  };
}

function createTrigger(url) {
  const listeners = new Map();
  return {
    dataset: { ghpGuestDetailUrl: url },
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
    dispatchEvent(event) {
      listeners.get(event.type)?.call(this, event);
    },
  };
}

function createHeroPoolPage(trigger) {
  const activeChip = {
    dataset: { statusFilter: "all" },
    classList: createClassList(),
    addEventListener(type, listener) {
      this[`on${type}`] = listener;
    },
  };
  const row = {
    dataset: { statusKey: "all", searchText: "测试门客 测试玩家" },
    hidden: false,
  };
  const searchInput = {
    value: "",
    dataset: {},
    addEventListener(type, listener) {
      this[`on${type}`] = listener;
    },
  };
  const filterEmptyState = { hidden: true };
  const slotTab = {
    dataset: { ghpSlotTarget: "1" },
    classList: createClassList(),
    setAttribute() {},
    addEventListener() {},
  };
  const slotCard = { dataset: { ghpSlotCard: "1" }, hidden: false };

  return {
    dataset: { filterCountJoinable: "1" },
    querySelector(selector) {
      if (selector === ".ghp-search-input") return searchInput;
      if (selector === ".ghp-filter-empty") return filterEmptyState;
      if (selector === ".ghp-chip.is-active") return activeChip;
      if (selector === '[data-ghp-slot-target][aria-pressed="true"]') return slotTab;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === ".ghp-chip") return [activeChip];
      if (selector === ".ghp-roster-row") return [row];
      if (selector === "[data-ghp-slot-target]") return [slotTab];
      if (selector === "[data-ghp-slot-card]") return [slotCard];
      if (selector === "[data-ghp-guest-detail-url]") return [trigger];
      return [];
    },
  };
}

test("guest detail trigger opens the guild modal and loads the read-only fragment", async () => {
  const scriptPath = path.resolve(__dirname, "..", "guild-hero-pool.js");
  const scriptSource = fs.readFileSync(scriptPath, "utf8");
  const trigger = createTrigger("/guilds/hero-pool/guest/7/");
  const page = createHeroPoolPage(trigger);
  const detailModal = {
    style: { display: "none" },
    attributes: {},
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
  };
  const detailBody = { innerHTML: "" };
  const documentListeners = new Map();
  const documentObj = {
    querySelector(selector) {
      if (selector === '[data-ghp-page="hero-pool"]') return page;
      if (selector === "[data-ghp-guest-detail-modal]") return detailModal;
      return null;
    },
    getElementById(id) {
      return id === "ghp-guest-detail-body" ? detailBody : null;
    },
    addEventListener(type, listener) {
      documentListeners.set(type, listener);
    },
    dispatchEvent(event) {
      documentListeners.get(event.type)?.call(this, event);
    },
  };
  const fetchCalls = [];
  const tooltipCalls = [];
  let openedTrigger = null;
  const context = {
    AbortController,
    document: documentObj,
    fetch: async (url, options) => {
      fetchCalls.push({ url, options });
      return {
        ok: true,
        text: async () => "<section>门客详情片段</section>",
      };
    },
    window: {
      initItemTooltip(options) {
        tooltipCalls.push(options);
      },
      GuildModal: {
        open(_modal, openedBy) {
          openedTrigger = openedBy;
          detailModal.style.display = "flex";
          return true;
        },
      },
    },
  };
  vm.createContext(context);
  vm.runInContext(scriptSource, context, { filename: "guild-hero-pool.js" });

  documentObj.dispatchEvent({ type: "DOMContentLoaded" });
  trigger.dispatchEvent({ type: "click" });
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(openedTrigger, trigger);
  assert.equal(detailModal.style.display, "flex");
  assert.equal(detailBody.innerHTML, "<section>门客详情片段</section>");
  assert.equal(fetchCalls.length, 1);
  assert.equal(fetchCalls[0].url, "/guilds/hero-pool/guest/7/");
  assert.equal(fetchCalls[0].options.credentials, "same-origin");
  assert.equal(fetchCalls[0].options.headers.Accept, "text/html");
  assert.deepEqual(tooltipCalls.map((options) => options.key), [
    "guild_hero_pool_guest_attributes",
    "guild_hero_pool_guest_equipment",
  ]);
});
