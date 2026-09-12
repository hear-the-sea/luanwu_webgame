const test = require("node:test");
const assert = require("node:assert/strict");

const tooltip = require("../tooltip.js");

test("createRelativeAnchor stores pointer offset within the cell bounds", () => {
  const anchor = tooltip.createRelativeAnchor(
    { left: 100, top: 50, width: 80, height: 40 },
    130,
    70
  );

  assert.deepEqual(anchor, { relativeX: 30, relativeY: 20 });
});

test("resolveAnchorPoint falls back to the cell bottom-left when no pointer anchor exists", () => {
  const point = tooltip.resolveAnchorPoint(
    { left: 24, top: 18, width: 100, height: 36, bottom: 54 },
    null
  );

  assert.deepEqual(point, { x: 24, y: 54 });
});

test("computeTooltipPosition flips above and left when the anchor is near viewport edges", () => {
  const position = tooltip.computeTooltipPosition({
    anchorX: 310,
    anchorY: 230,
    tooltipWidth: 120,
    tooltipHeight: 90,
    viewportWidth: 360,
    viewportHeight: 260,
    viewportPadding: 16,
    offset: 8,
  });

  assert.deepEqual(position, { left: 182, top: 132 });
});

test("computeTooltipPosition keeps the tooltip inside viewport padding when space is tight", () => {
  const position = tooltip.computeTooltipPosition({
    anchorX: 10,
    anchorY: 10,
    tooltipWidth: 140,
    tooltipHeight: 120,
    viewportWidth: 160,
    viewportHeight: 150,
    viewportPadding: 12,
    offset: 8,
  });

  assert.deepEqual(position, { left: 12, top: 18 });
});

test("resolveAnchorPoint uses the cell bottom-left when pointer tracking is disabled", () => {
  const point = tooltip.resolveAnchorPoint(
    { left: 40, top: 20, width: 100, height: 30, bottom: 50 },
    null
  );

  assert.deepEqual(point, { x: 40, y: 50 });
});

test("initTooltip skips mousemove listener when pointer tracking is disabled", () => {
  const listeners = [];
  const previousDocument = global.document;
  const previousMatchMedia = global.matchMedia;
  const previousAddEventListener = global.addEventListener;
  const previousRequestAnimationFrame = global.requestAnimationFrame;
  const previousRegistry = global.__webgame_tooltip;

  global.document = {
    querySelector(selector) {
      return selector === ".guest-equip-tooltip-trigger" ? {} : null;
    },
    addEventListener(type) {
      listeners.push(type);
    },
  };
  global.matchMedia = () => ({ matches: true });
  global.addEventListener = () => {};
  global.requestAnimationFrame = (callback) => {
    callback();
    return 1;
  };
  global.__webgame_tooltip = {};

  tooltip.initTooltip({
    key: "guest_detail_test",
    cellSelector: ".guest-equip-tooltip-trigger",
    tooltipSelector: ".guest-equip-tooltip-bubble",
    trackPointer: false,
  });

  global.document = previousDocument;
  global.matchMedia = previousMatchMedia;
  global.addEventListener = previousAddEventListener;
  global.requestAnimationFrame = previousRequestAnimationFrame;
  global.__webgame_tooltip = previousRegistry;

  assert.ok(listeners.includes("mouseover"));
  assert.ok(!listeners.includes("mousemove"));
});

test("touch tooltips avoid label selection and support hybrid pointers", () => {
  const listeners = new Map();
  const previousDocument = global.document;
  const previousMatchMedia = global.matchMedia;
  const previousAddEventListener = global.addEventListener;
  const previousRequestAnimationFrame = global.requestAnimationFrame;

  const classNames = new Set();
  const control = {
    closest(selector) {
      return selector === ".guest-equip-tooltip-trigger" ? cell : null;
    },
    contains() {
      return false;
    },
  };
  const cell = {
    tagName: "LABEL",
    control,
    classList: {
      add(name) {
        classNames.add(name);
      },
      remove(name) {
        classNames.delete(name);
      },
      contains(name) {
        return classNames.has(name);
      },
    },
    getBoundingClientRect() {
      return { left: 0, top: 0, width: 100, height: 32, bottom: 32 };
    },
    querySelector() {
      return { style: {} };
    },
  };
  const labelText = {
    closest(selector) {
      return selector === ".guest-equip-tooltip-trigger" ? cell : null;
    },
  };

  global.document = {
    querySelector(selector) {
      return selector === ".guest-equip-tooltip-trigger" ? cell : null;
    },
    addEventListener(type, handler) {
      listeners.set(type, handler);
    },
  };
  global.matchMedia = (query) => ({
    matches: query === "(hover: hover) and (pointer: fine)" || query === "(any-pointer: coarse)",
  });
  global.addEventListener = () => {};
  global.requestAnimationFrame = () => 1;
  try {
    tooltip.initTooltip({
      key: "guest_detail_label_forwarding_test",
      cellSelector: ".guest-equip-tooltip-trigger",
      tooltipSelector: ".guest-equip-tooltip-bubble",
      trackPointer: false,
    });

    const clickHandler = listeners.get("click");
    assert.equal(typeof clickHandler, "function");

    // Hybrid devices can emit a mouseover before the touch-generated click.
    listeners.get("mouseover")({ target: labelText });
    let labelClickPrevented = false;
    clickHandler({
      target: labelText,
      preventDefault() {
        labelClickPrevented = true;
      },
    });
    assert.equal(labelClickPrevented, true);
    assert.equal(classNames.has("is-tooltip-active"), true);

    clickHandler({
      target: labelText,
      preventDefault() {
        labelClickPrevented = true;
      },
    });
    assert.equal(classNames.has("is-tooltip-active"), false);

    let controlClickPrevented = false;
    clickHandler({
      target: control,
      preventDefault() {
        controlClickPrevented = true;
      },
    });
    assert.equal(controlClickPrevented, false);
    assert.equal(classNames.has("is-tooltip-active"), true);
  } finally {
    global.document = previousDocument;
    global.matchMedia = previousMatchMedia;
    global.addEventListener = previousAddEventListener;
    global.requestAnimationFrame = previousRequestAnimationFrame;
  }
});

test("eventTargetMatchesIgnore detects ignored tooltip controls", () => {
  const ignoredTarget = {
    closest(selector) {
      return selector === ".tooltip-ignore" ? {} : null;
    },
  };
  const regularTarget = {
    closest() {
      return null;
    },
  };

  assert.equal(tooltip.eventTargetMatchesIgnore(ignoredTarget, ".tooltip-ignore"), true);
  assert.equal(tooltip.eventTargetMatchesIgnore(regularTarget, ".tooltip-ignore"), false);
  assert.equal(tooltip.eventTargetMatchesIgnore(ignoredTarget, ""), false);
});

test("syncTooltipContent fills and clears tooltip text from an attribute", () => {
  const tooltipElem = { textContent: "" };
  const cell = {
    getAttribute(name) {
      return name === "data-tooltip-text" ? "武力：81" : "";
    },
    querySelector(selector) {
      return selector === ".guest-attribute-tooltip-bubble" ? tooltipElem : null;
    },
  };

  tooltip.syncTooltipContent(cell, ".guest-attribute-tooltip-bubble", "data-tooltip-text");
  assert.equal(tooltipElem.textContent, "武力：81");

  tooltip.clearTooltipContent(cell, ".guest-attribute-tooltip-bubble", "data-tooltip-text");
  assert.equal(tooltipElem.textContent, "");
});
