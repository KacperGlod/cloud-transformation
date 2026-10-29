import test from "node:test";
import assert from "node:assert/strict";
import * as THREE from "../libs/three.js/build/three.module.js";
import {LayerRenderer} from "../src/viewer/LayerRenderer.js";

const cloud = (layerOrder, color, opacity = 1) => ({
  visible: true, userData: {layerOrder}, material: {opacity}, color,
});
const factor = (type, alpha) => {
  if (type === THREE.OneFactor) return 1;
  if (type === THREE.SrcAlphaFactor) return alpha;
  if (type === THREE.OneMinusSrcAlphaFactor) return 1 - alpha;
  throw new Error(`Unsupported test blend factor: ${type}`);
};

// The renderer's actual blend factors drive this single-pixel compositor.
// Browser QA additionally checks its GLSL output against real point-cloud images.
function setup(t, clouds) {
  const background = [.1, .2, .3, 1], passes = [];
  let pixel, target = null, clearColor = new THREE.Color(.1, .2, .3), clearAlpha = 1, overlays = 0;
  const renderer = {
    getSize: result => result.set(20, 10),
    getClearColor: result => result.copy(clearColor), getClearAlpha: () => clearAlpha,
    setClearColor(color, alpha) { clearColor = new THREE.Color(color); clearAlpha = alpha; },
    setRenderTarget(value) { target = value; },
    clear() { if (target) target.texture.testPixel = [0, 0, 0, 0]; },
    render(scene) {
      const material = scene.children[0].material;
      const source = material.uniforms.layerColor.value.testPixel;
      const alpha = source[3] * material.uniforms.opacity.value;
      if (!alpha) return;
      const next = [...pixel];
      for (let i = 0; i < 3; i++) next[i] = source[i] * factor(material.blendSrc, alpha) + pixel[i] * factor(material.blendDst, alpha);
      next[3] = alpha * factor(material.blendSrcAlpha, alpha) + pixel[3] * factor(material.blendDstAlpha, alpha);
      pixel = next;
    },
  };
  const base = {render(params) {
    assert.equal(params.pointclouds.length, 1);
    const pointcloud = params.pointclouds[0];
    passes.push({pointcloud, target: params.layerTarget});
    params.layerTarget.texture.testPixel = [...pointcloud.color];
  }};
  const viewer = {renderer, scene: {pointclouds: clouds, getActiveCamera: () => ({})}, dispatchEvent() {},
    getPRenderer(options) { assert.equal(options.compositeLayers, false); return base; }};
  const layers = new LayerRenderer(viewer);
  layers.regularRenderer = {clear() {}, renderBackground() { pixel = [...background]; }, renderScene() { overlays++; }};
  t.after(() => layers.dispose());
  return {layers, renderer, base, passes, background, pixel: () => pixel, overlays: () => overlays,
    target: () => target, clearColor: () => clearColor, clearAlpha: () => clearAlpha};
}

function close(actual, expected) {
  for (let i = 0; i < actual.length; i++) assert.ok(Math.abs(actual[i] - expected[i]) < 1e-8, `${actual} != ${expected}`);
}

test("the complete layer fades linearly, including the 100% to 99% visibility boundary", t => {
  const pointcloud = cloud(0, [.9, .7, .5, 1]);
  const state = setup(t, [pointcloud]);
  for (const opacity of [1, .99, .98, .5, .01, 0]) {
    pointcloud.material.opacity = opacity;
    state.layers.render({});
    close(state.pixel(), pointcloud.color.map((value, i) => i === 3 ? 1 : value * opacity + state.background[i] * (1 - opacity)));
  }
});

test("list order stays fixed as opacity changes and transparent pixels reveal lower layers", t => {
  const top = cloud(0, [1, 0, 0, 1], .5), bottom = cloud(1, [0, 1, 0, 1]);
  const state = setup(t, [top, bottom]);
  state.layers.render({});
  close(state.pixel(), [.5, .5, 0, 1]);
  assert.deepEqual(state.passes.map(pass => pass.pointcloud), [bottom, top]);
  top.material.opacity = .99;
  state.layers.render({});
  close(state.pixel(), [.99, .01, 0, 1]);
  top.color[3] = 0;
  state.layers.render({});
  close(state.pixel(), [0, 1, 0, 1]);
});

test("hidden and zero-opacity clouds do not render and overlays render only once", t => {
  const hidden = cloud(0, [1, 0, 0, 1]); hidden.visible = false;
  const state = setup(t, [hidden, cloud(1, [0, 1, 0, 1], 0), cloud(2, [0, 0, 1, 1])]);
  state.layers.render({});
  assert.equal(state.passes.length, 1);
  assert.equal(state.overlays(), 1);
  close(state.pixel(), [0, 0, 1, 1]);
});

test("clouds and frames share one offscreen target", t => {
  const state = setup(t, [cloud(0, [1, 0, 0, 1]), cloud(1, [0, 1, 0, 1])]);
  state.layers.render({}); state.layers.render({});
  assert.equal(new Set(state.passes.map(pass => pass.target)).size, 1);
  assert.equal(state.layers.target.width, 20);
  assert.equal(state.layers.target.height, 10);
});

test("failed rendering restores the original cloud material, framebuffer and clear state", t => {
  const pointcloud = cloud(0, [1, 0, 0, 1]);
  const material = pointcloud.material, state = setup(t, [pointcloud]);
  state.base.render = () => { pointcloud.material = {temporary: true}; throw new Error("GPU failure"); };
  assert.throws(() => state.layers.render({}), /GPU failure/);
  assert.equal(pointcloud.material, material);
  assert.equal(state.target(), null);
  close(state.clearColor().toArray(), [.1, .2, .3]);
  assert.equal(state.clearAlpha(), 1);
});
