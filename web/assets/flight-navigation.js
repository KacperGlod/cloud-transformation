export function resumeAfterFlight(viewer, controls, previousCameraMode, flightStarted) {
  // Resume navigation at the flight's final pose, with no leftover drag or inertia.
  controls.stop?.();
  controls.stopTweens?.();
  viewer.inputHandler.drag = null;
  viewer.inputHandler.pressedKeys = {};
  viewer.setControls(controls);

  // Returning to an orthographic projection changes the apparent viewpoint.
  // Restore it only when pointer lock failed or was cancelled before flight.
  if (!flightStarted && previousCameraMode !== null) {
    viewer.setCameraMode(previousCameraMode);
  }
}
