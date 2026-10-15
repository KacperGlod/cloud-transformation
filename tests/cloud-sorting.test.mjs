import test from "node:test";
import assert from "node:assert/strict";
import {sortClouds} from "../web/assets/cloud-sorting.js";

function cloud(id, attributes = {}) {
  return {id, name: "Skan", status: "ready", visibility: "private", sizeBytes: 0,
    createdAt: "2026-10-16T12:00:00Z", ...attributes};
}

const ids = clouds => clouds.map(cloud => cloud.id);

test("names follow Polish collation and natural numbers regardless of case", () => {
  const clouds = [cloud("z", {name: "Zamek"}), cloud("10", {name: "skan 10"}),
    cloud("l", {name: "Łąka"}), cloud("2", {name: "Skan 2"})];
  assert.deepEqual(ids(sortClouds(clouds, "name", "ascending")), ["l", "2", "10", "z"]);
  assert.deepEqual(ids(sortClouds(clouds, "name", "descending")), ["z", "10", "2", "l"]);
});

test("statuses follow displayed labels rather than API values", () => {
  const clouds = [cloud("q", {status: "queued"}), cloud("c", {status: "converting"}),
    cloud("r", {status: "ready"}), cloud("f", {status: "failed"})];
  assert.deepEqual(ids(sortClouds(clouds, "status", "ascending")), ["f", "r", "c", "q"]);
  assert.deepEqual(ids(sortClouds(clouds, "status", "descending")), ["q", "c", "r", "f"]);
});

test("visibility sorts private and public clouds in both directions", () => {
  const clouds = [cloud("public", {visibility: "public"}), cloud("private")];
  assert.deepEqual(ids(sortClouds(clouds, "visibility", "ascending")), ["private", "public"]);
  assert.deepEqual(ids(sortClouds(clouds, "visibility", "descending")), ["public", "private"]);
});

test("source sizes compare actual bytes across units, including zero", () => {
  const clouds = [cloud("mb", {sizeBytes: 2 * 1024 ** 2}), cloud("b", {sizeBytes: 900}),
    cloud("kb", {sizeBytes: 10 * 1024}), cloud("zero")];
  assert.deepEqual(ids(sortClouds(clouds, "sizeBytes", "ascending")), ["zero", "b", "kb", "mb"]);
  assert.deepEqual(ids(sortClouds(clouds, "sizeBytes", "descending")), ["mb", "kb", "b", "zero"]);
});

test("upload dates compare full instants across days and timezone offsets", () => {
  const clouds = [cloud("earlier-day", {createdAt: "2026-09-30T23:59:00Z"}),
    cloud("earlier-minute", {createdAt: "2026-10-01T12:00:00+02:00"}),
    cloud("later-minute", {createdAt: "2026-10-01T10:01:00Z"}),
    cloud("later-second", {createdAt: "2026-10-01T10:01:01Z"})];
  assert.deepEqual(ids(sortClouds(clouds, "createdAt", "ascending")),
    ["earlier-day", "earlier-minute", "later-minute", "later-second"]);
  assert.deepEqual(ids(sortClouds(clouds, "createdAt", "descending")),
    ["later-second", "later-minute", "earlier-minute", "earlier-day"]);
});

test("ties stay deterministic after refresh and the source list is unchanged", () => {
  const clouds = [cloud("old", {createdAt: "2026-10-15T12:00:00Z"}), cloud("b"), cloud("a")];
  const original = [...clouds];
  for (const direction of ["ascending", "descending"]) {
    assert.deepEqual(ids(sortClouds(clouds, "name", direction)), ["a", "b", "old"]);
    assert.deepEqual(ids(sortClouds([...clouds].reverse(), "name", direction)), ["a", "b", "old"]);
  }
  assert.deepEqual(clouds, original);
  assert.deepEqual(sortClouds([], "name", "ascending"), []);
  assert.deepEqual(sortClouds([clouds[0]], "createdAt", "descending"), [clouds[0]]);
});
