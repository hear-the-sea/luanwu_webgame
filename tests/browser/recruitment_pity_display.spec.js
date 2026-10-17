const { test, expect } = require("@playwright/test");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");

test("pity is hidden and candidate rarity follows identification rules", async ({ page }) => {
  await page.setContent(`
    <section id="recruit-pools-section"></section>
    <section id="recruit-candidates-section" data-candidate-count="3">
      <form id="candidate-selected-form">
        <div id="candidate-selected-ids"></div><div id="candidate-list"></div>
      </form>
      <script id="recruit-candidates-data" type="application/json">${JSON.stringify([
        { id: 1, display_name: "保底候选", rarity: "orange", rarity_revealed: false, pity_rarity: "orange" },
        { id: 2, display_name: "普通候选", rarity: "purple", rarity_revealed: false, pity_rarity: "" },
        { id: 3, display_name: "已鉴定候选", rarity: "purple", rarity_revealed: true, pity_rarity: "purple" },
      ])}</script>
    </section>`);
  await page.addScriptTag({ path: path.join(root, "static/js/recruitment_hall_core.js") });
  await page.addScriptTag({ path: path.join(root, "static/js/recruitment-hall.js") });
  await page.evaluate(() => document.dispatchEvent(new Event("partial-nav:loaded")));

  await expect(page.locator("#candidate-list label")).toHaveCount(3);
  await expect(page.locator("#candidate-list").getByText("保底获得", { exact: true })).toHaveCount(0);
  await expect(page.getByText("保底候选", { exact: true })).not.toHaveClass(/rarity-text-/);
  await expect(page.getByText("普通候选", { exact: true })).not.toHaveClass(/rarity-text-/);
  await expect(page.getByText("已鉴定候选", { exact: true })).toHaveClass(/rarity-text-purple/);
});
