import assert from "node:assert/strict";
import test from "node:test";
import {createPointcloudCache} from "../web/assets/pointcloud-cache.js";

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};

test("concurrent scene requests share a cloud and attach it once", async () => {
  const request = deferred(), attached = [];
  let loads = 0;
  const cache = createPointcloudCache({load() { loads++; return request.promise; }, prepare: cloud => attached.push(cloud)});
  const first = cache.get("/a/metadata.json", "A"), second = cache.get("/a/metadata.json", "A");
  const cloud = {name: "A"};
  request.resolve(cloud);
  assert.equal(await first, cloud);
  assert.equal(await second, cloud);
  assert.equal(await cache.get("/a/metadata.json", "Renamed"), cloud);
  assert.equal(loads, 1);
  assert.deepEqual(attached, [cloud]);
  assert.deepEqual([...cache.values()], [cloud]);
});

test("a failed layer can be retried without discarding other cached layers", async () => {
  const cloud = {}, attached = [];
  let attempts = 0;
  const cache = createPointcloudCache({
    load(url) { if (url === "bad" && ++attempts === 1) throw new Error("No access"); return cloud; },
    prepare: item => attached.push(item),
  });
  await cache.get("good", "Good");
  await assert.rejects(cache.get("bad", "Bad"), /No access/);
  assert.equal(await cache.get("bad", "Bad"), cloud);
  assert.equal(await cache.get("good", "Good"), cloud);
  assert.equal(attempts, 2);
  assert.equal(attached.length, 2);
});

test("a result arriving after timeout cannot attach or replace its successful retry", async () => {
  const stale = deferred(), attached = [], fresh = {name: "fresh"};
  let attempts = 0;
  const cache = createPointcloudCache({load: () => ++attempts === 1 ? stale.promise : fresh,
    prepare: item => attached.push(item), timeoutMs: 10});
  await assert.rejects(cache.get("a", "A"), /Przekroczono czas/);
  assert.equal(await cache.get("a", "A"), fresh);
  stale.resolve({name: "stale"});
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(attached, [fresh]);
  assert.equal(await cache.get("a", "A"), fresh);
});

test("attachment errors reject the load and allow another attempt", async () => {
  let attempts = 0;
  const cache = createPointcloudCache({load: () => ({}), prepare() { if (++attempts === 1) throw new Error("Scene unavailable"); }});
  await assert.rejects(cache.get("a", "A"), /Scene unavailable/);
  assert.ok(await cache.get("a", "A"));
  assert.equal(attempts, 2);
});
