import test from "node:test";
import assert from "node:assert/strict";
import {View} from "../src/viewer/View.js";
import {applyCamera, combinedBounds, ComparisonController, readCamera} from "../web/assets/comparison-camera.js";

const camera = (x = 0) => ({position: [x, 20, 30], yaw: 0.3, pitch: -0.5, radius: 40,
  fov: 60, cameraMode: 1, moveSpeed: 10});
const sourceBounds = {min: [500000, 6000000, 0], max: [500010, 6000010, 20]};
const resultBounds = {min: [500100, 6000002, 5], max: [500110, 6000012, 25]};

function setup() {
  const sent = [];
  const controller = new ComparisonController((index, message) => sent.push({index, ...message}));
  controller.receive(0, {type: "ready", camera: camera(), bounds: sourceBounds});
  controller.receive(1, {type: "ready", camera: camera(10), bounds: resultBounds});
  return {controller, sent};
}

test("waits for both clouds and frames their actual combined coordinates", () => {
  const sent = [];
  const controller = new ComparisonController((index, message) => sent.push({index, ...message}));
  controller.receive(1, {type: "ready", camera: camera(), bounds: resultBounds});
  assert.equal(controller.ready, false);
  assert.equal(sent.length, 0);
  controller.receive(0, {type: "ready", camera: camera(), bounds: sourceBounds});
  assert.equal(controller.ready, true);
  assert.deepEqual(sent, [{index: 0, type: "fit", bounds: {
    min: [500000, 6000000, 0], max: [500110, 6000012, 25],
  }}]);
});

test("camera control transfers between panels without echoing remote updates", () => {
  const {controller, sent} = setup();
  sent.length = 0;
  controller.receive(0, {type: "camera", camera: camera(123)});
  assert.deepEqual(sent, [{index: 1, type: "camera", camera: camera(123)}]);
  controller.receive(1, {type: "camera", camera: camera(123)});
  assert.equal(sent.length, 1);
  controller.receive(1, {type: "interact"});
  controller.receive(1, {type: "camera", camera: camera(456)});
  assert.deepEqual(sent[1], {index: 0, type: "camera", camera: camera(456)});
  controller.receive(0, {type: "camera", camera: camera(456)});
  assert.equal(sent.length, 2);
});

test("independent mode fits each cloud and enabling sync adopts the last active camera", () => {
  const {controller, sent} = setup();
  controller.setSynchronized(false);
  sent.length = 0;
  controller.receive(1, {type: "interact"});
  controller.receive(1, {type: "camera", camera: camera(789)});
  assert.equal(sent.length, 0);
  controller.fit();
  assert.deepEqual(sent, [
    {index: 0, type: "fit", bounds: sourceBounds},
    {index: 1, type: "fit", bounds: resultBounds},
  ]);
  controller.setSynchronized(true);
  assert.deepEqual(sent[2], {index: 0, type: "camera", camera: camera(789)});
});

test("invalid messages cannot change or relay the shared camera", () => {
  const {controller, sent} = setup();
  sent.length = 0;
  for (const invalid of [null, {}, {...camera(), radius: -1}, {...camera(), position: [NaN, 0, 0]},
    {...camera(), cameraMode: 2}, {...camera(), fov: Infinity}]) {
    controller.receive(0, {type: "camera", camera: invalid});
  }
  controller.receive(-1, {type: "interact"});
  assert.equal(controller.active, 0);
  assert.deepEqual(sent, []);
  assert.equal(combinedBounds([{min: [10, 0, 0], max: [0, 1, 1]}]), null);
});

test("restores perspective and orthographic camera state including pivot distance", () => {
  const viewer = {
    scene: {view: new View(), cameraMode: 1}, fov: 40, speed: 0,
    getFOV() { return this.fov; }, setFOV(value) { this.fov = value; },
    getMoveSpeed() { return this.speed; }, setMoveSpeed(value) { this.speed = value; },
    setCameraMode(value) { this.scene.cameraMode = value; },
  };
  for (const mode of [0, 1]) {
    const state = {...camera(500000), cameraMode: mode};
    assert.equal(applyCamera(viewer, state), true);
    assert.deepEqual(readCamera(viewer), state);
    const before = viewer.scene.view.getPivot().toArray();
    assert.equal(applyCamera(viewer, {...state, position: [1, 2]}), false);
    assert.deepEqual(viewer.scene.view.getPivot().toArray(), before);
  }
});
