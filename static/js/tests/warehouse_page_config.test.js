const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

function readScript(relativePath) {
  return fs.readFileSync(path.resolve(__dirname, "..", relativePath), "utf8");
}

test("xisuidan modal shows the current qualification hint", () => {
  const source = readScript("warehouse-page.js");

  assert.match(
    source,
    /xisuidan:\s*\{[\s\S]*hint:\s*"洗髓丹能提升门客的资质，但成长方向可能发生某些未知改变哦~仅满级门客能够使用"[\s\S]*\}/
  );
});
