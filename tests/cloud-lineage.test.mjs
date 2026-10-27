import assert from "node:assert/strict";
import test from "node:test";
import {operationLabel, orderedLineage} from "../web/assets/cloud-lineage.js";

test("lineage groups children under parents while preserving sibling order", () => {
  const nodes = [{id: "child", parentId: "root"}, {id: "sibling", parentId: "root"},
    {id: "root", parentId: null}, {id: "result", parentId: "child"}, {id: "orphan", parentId: "missing"}];
  assert.deepEqual(orderedLineage(nodes).map(({node, depth}) => [node.id, depth]),
    [["root", 0], ["child", 1], ["result", 2], ["sibling", 1], ["orphan", 0]]);
});

test("cycles, self links, and deep chains visit each record exactly once", () => {
  const cycle = [{id: "a", parentId: "b"}, {id: "b", parentId: "a"}, {id: "self", parentId: "self"}];
  assert.equal(new Set(orderedLineage(cycle).map(item => item.node.id)).size, 3);
  const chain = Array.from({length: 10000}, (_, id) => ({id, parentId: id ? id - 1 : null}));
  const ordered = orderedLineage(chain);
  assert.equal(ordered.length, chain.length);
  assert.equal(ordered.at(-1).depth, chain.length - 1);
  assert.deepEqual(orderedLineage([]), []);
});

test("operation labels handle unknown and legacy values", () => {
  assert.equal(operationLabel("segmentation"), "Segmentacja");
  assert.equal(operationLabel("transformation"), "Transformacja");
  assert.equal(operationLabel("point-removal"), "Usuwanie punktów");
  assert.equal(operationLabel("import"), "Import");
  for (const value of [undefined, "toString", "__proto__", "unknown"]) assert.equal(operationLabel(value), "Przetwarzanie");
});
