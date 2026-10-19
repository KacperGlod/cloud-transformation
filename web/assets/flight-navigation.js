import * as THREE from "../../libs/three.js/build/three.module.js";

const validRadius = value => Number.isFinite(value) && value > 0.000001;

export function getFlightOrbitRadius(viewer, pointcloud, previousRadius, pickPoint) {
  const fallback = validRadius(previousRadius) ? previousRadius : 1;
  if (!pointcloud || pointcloud.visible === false) return fallback;
  const view = viewer.scene.view;
  const direction = view.direction;
  const canvas = viewer.renderer.domElement;

  if (canvas.clientWidth > 0 && canvas.clientHeight > 0) {
    // Mouse events can finish flight between frames; pick from the final pose.
    const camera = viewer.scene.cameraP.clone();
    camera.position.copy(view.position);
    camera.rotation.set(Math.PI / 2 + view.pitch, 0, view.yaw, "ZXY");
    camera.updateMatrixWorld(true);
    const center = new THREE.Vector2(canvas.clientWidth / 2, canvas.clientHeight / 2);
    let hit;
    try {
      hit = pickPoint(center, camera, viewer, [pointcloud]);
    } catch (error) {
      // GPU picking may be unavailable after losing focus or the WebGL context.
      console.warn("Could not pick the orbit target after flight; using cloud bounds.", error);
    }
    if (hit) {
      const depth = hit.location.clone().sub(view.position).dot(direction);
      if (validRadius(depth)) return depth;
    }
  }

  // A sparse cloud may have no point under the crosshair. Use only its world
  // bounds, excluding cached clouds, and retain the old radius when looking away.
  const bounds = viewer.getBoundingBox([pointcloud]);
  if (!bounds.isEmpty()) {
    const hit = new THREE.Ray(view.position, direction).intersectBox(bounds, new THREE.Vector3());
    if (hit) {
      const distance = hit.distanceTo(view.position);
      if (validRadius(distance)) return distance;
    }
  }
  return fallback;
}

export function resumeAfterFlight(viewer, controls, previousCameraMode, flightStarted, orbitRadius = null) {
  // Resume navigation at the flight's final pose, with no leftover drag or inertia.
  controls.stop?.();
  controls.stopTweens?.();
  viewer.inputHandler.drag = null;
  viewer.inputHandler.pressedKeys = {};
  if (flightStarted && validRadius(orbitRadius)) {
    // Orbit motion is proportional to this distance, not to flight speed.
    viewer.scene.view.radius = orbitRadius;
    viewer.setMoveSpeed(orbitRadius);
  }
  viewer.setControls(controls);

  // Returning to an orthographic projection changes the apparent viewpoint.
  // Restore it only when pointer lock failed or was cancelled before flight.
  if (!flightStarted && previousCameraMode !== null) {
    viewer.setCameraMode(previousCameraMode);
  }
}
