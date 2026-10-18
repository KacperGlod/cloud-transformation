const vector = value => Array.isArray(value) && value.length === 3 && value.every(Number.isFinite);

export function validCamera(state) {
  return Boolean(state && vector(state.position)
    && [state.yaw, state.pitch, state.radius, state.fov, state.moveSpeed].every(Number.isFinite)
    && state.radius > 0 && state.moveSpeed >= 0 && state.fov > 0 && state.fov < 180
    && Math.abs(state.pitch) <= Math.PI / 2 && [0, 1].includes(state.cameraMode));
}

export function readCamera(viewer) {
  const view = viewer.scene.view;
  return {
    position: view.position.toArray(), yaw: view.yaw, pitch: view.pitch, radius: view.radius,
    fov: viewer.getFOV(), cameraMode: viewer.scene.cameraMode, moveSpeed: viewer.getMoveSpeed(),
  };
}

export function applyCamera(viewer, state) {
  if (!validCamera(state)) return false;
  viewer.controls?.stop?.();
  viewer.controls?.stopTweens?.();
  const view = viewer.scene.view;
  view.position.fromArray(state.position);
  view.yaw = state.yaw;
  view.pitch = state.pitch;
  view.radius = state.radius;
  viewer.setFOV(state.fov);
  viewer.setCameraMode(state.cameraMode);
  viewer.setMoveSpeed(state.moveSpeed);
  return true;
}

export function combinedBounds(bounds) {
  if (!bounds.length || bounds.some(box => !box || !vector(box.min) || !vector(box.max)
    || box.min.some((value, axis) => value > box.max[axis]))) return null;
  return {
    min: [0, 1, 2].map(axis => Math.min(...bounds.map(box => box.min[axis]))),
    max: [0, 1, 2].map(axis => Math.max(...bounds.map(box => box.max[axis]))),
  };
}

// One panel controls the shared camera until the user interacts with the other.
// Remote updates never acquire control, preventing feedback and fighting inertia.
export class ComparisonController {
  constructor(send) {
    this.send = send;
    this.panels = [null, null];
    this.active = 0;
    this.synchronized = true;
  }

  get ready() {
    return this.panels.every(Boolean);
  }

  receive(index, message) {
    if (![0, 1].includes(index) || !message) return;
    if (message.type === "ready") {
      if (!validCamera(message.camera) || !combinedBounds([message.bounds])) return;
      this.panels[index] = {camera: message.camera, bounds: message.bounds};
      if (this.ready) this.fit();
    } else if (message.type === "interact" && this.panels[index]) {
      this.active = index;
    } else if (message.type === "camera" && this.panels[index] && validCamera(message.camera)) {
      this.panels[index].camera = message.camera;
      if (this.ready && this.synchronized && index === this.active) {
        this.send(1 - index, {type: "camera", camera: message.camera});
      }
    }
  }

  setSynchronized(enabled) {
    this.synchronized = enabled;
    if (enabled && this.ready) {
      this.send(1 - this.active, {type: "camera", camera: this.panels[this.active].camera});
    }
  }

  fit() {
    if (!this.ready) return;
    if (this.synchronized) {
      this.send(this.active, {type: "fit", bounds: combinedBounds(this.panels.map(panel => panel.bounds))});
    } else {
      this.panels.forEach((panel, index) => this.send(index, {type: "fit", bounds: panel.bounds}));
    }
  }
}
