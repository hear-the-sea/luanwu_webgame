const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const GUILD_TEMPLATE_PATHS = [
  "applications.html",
  "announcements.html",
  "detail.html",
  "info.html",
  "members.html",
  "technology.html",
].map((name) => path.resolve(__dirname, "../../..", "guilds/templates/guilds", name));
const TRADE_TEMPLATE_PATHS = [
  "_auction.html",
  "_market.html",
  "_shop.html",
].map((name) => path.resolve(__dirname, "../../..", "trade/templates/trade/partials", name));

test("player pages use external scripts without inline handlers", () => {
  const baseTemplate = fs.readFileSync(
    path.resolve(__dirname, "../../..", "guilds/templates/guilds/base.html"),
    "utf8",
  );
  assert.match(baseTemplate, /static 'js\/guild-page\.js'/);

  const tradePageTemplate = fs.readFileSync(
    path.resolve(__dirname, "../../..", "trade/templates/trade/trade.html"),
    "utf8",
  );
  assert.match(tradePageTemplate, /static 'js\/trade\.js'/);

  [...GUILD_TEMPLATE_PATHS, ...TRADE_TEMPLATE_PATHS].forEach((templatePath) => {
    const content = fs.readFileSync(templatePath, "utf8");
    assert.doesNotMatch(content, /<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?<\/script>/i);
    assert.doesNotMatch(content, /\bon(?:click|change|input|submit)=/i);
  });
});
