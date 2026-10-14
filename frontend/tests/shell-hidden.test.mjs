import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

test("hidden attribute is not overridden by component display rules", () => {
  const css = fs.readFileSync(new URL("../styles/main.css", import.meta.url), "utf8");
  assert.match(css, /\[hidden\]\s*\{\s*display:\s*none\s*!important/);
});
