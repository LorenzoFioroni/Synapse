import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { computeChanges } from "../../frontend/js/views/metadataReview.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const casesFile = path.resolve(__dirname, "../fixtures/metadata_diff_cases.json");
const cases = JSON.parse(fs.readFileSync(casesFile, "utf-8"));

for (const c of cases) {
  test(`computeChanges parity: ${c.name}`, () => {
    const changes = computeChanges(c.current, c.fetched);
    assert.equal(changes.length, c.expected.length, `${c.name}: count mismatch`);
    for (let i = 0; i < changes.length; i++) {
      const actual = changes[i];
      const exp = c.expected[i];
      assert.equal(actual.key, exp.key);
      assert.equal(String(actual.fetched), String(exp.fetched));
      assert.equal(actual.isNew, exp.isNew);
      assert.equal(actual.checked, exp.checked);
      if (exp.current === null) {
        assert.ok(actual.current === null || actual.current === undefined);
      } else {
        assert.equal(String(actual.current), String(exp.current));
      }
    }
  });
}
