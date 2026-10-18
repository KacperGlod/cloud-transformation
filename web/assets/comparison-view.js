import * as THREE from "/libs/three.js/build/three.module.js";
import {api} from "./common.js";
import {applyCamera, combinedBounds, readCamera} from "./comparison-camera.js";

const status = document.getElementById("view_status");
let viewer;
let ready = false;
let lastCamera = "";

function notify(message) {
  if (window.parent !== window) {
    window.parent.postMessage({channel: "cloudpoint-comparison", ...message}, window.location.origin);
  }
}

function showError(message) {
  ready = false;
  status.hidden = false;
  status.dataset.kind = "error";
  status.textContent = message;
  notify({type: "error", message});
}

function publishCamera() {
  if (!ready) return;
  const camera = readCamera(viewer);
  const serialized = JSON.stringify(camera);
  if (serialized !== lastCamera) {
    lastCamera = serialized;
    notify({type: "camera", camera});
  }
}

function fitBounds(bounds) {
  if (!combinedBounds([bounds])) return;
  const box = new THREE.Box3(new THREE.Vector3(...bounds.min), new THREE.Vector3(...bounds.max));
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(box.getBoundingSphere(new THREE.Sphere()).radius, 0.01);
  const verticalFov = viewer.getFOV() * Math.PI / 180;
  const aspect = Math.max(viewer.renderArea.clientWidth / Math.max(viewer.renderArea.clientHeight, 1), 0.01);
  const limitingFov = Math.min(verticalFov, 2 * Math.atan(Math.tan(verticalFov / 2) * aspect));
  const distance = radius / Math.sin(limitingFov / 2) * 1.1;
  viewer.controls.stop();
  viewer.controls.stopTweens?.();
  viewer.scene.view.position.copy(center).sub(viewer.scene.view.direction.multiplyScalar(distance));
  viewer.scene.view.radius = distance;
  viewer.setMoveSpeed(radius / 2);
  publishCamera();
}

window.addEventListener("message", event => {
  if (!ready || event.origin !== window.location.origin || event.source !== window.parent
    || event.data?.channel !== "cloudpoint-comparison") return;
  if (event.data.type === "camera" && applyCamera(viewer, event.data.camera)) {
    lastCamera = JSON.stringify(readCamera(viewer));
  } else if (event.data.type === "fit") {
    fitBounds(event.data.bounds);
  }
});

async function initialize() {
  let timeout;
  try {
    const identifier = new URLSearchParams(window.location.search).get("cloud");
    const cloud = await api(`/api/clouds/${encodeURIComponent(identifier)}`);
    if (cloud.status !== "ready" || !cloud.url) throw new Error("Chmura nie jest gotowa do wyświetlenia.");
    viewer = new Potree.Viewer(document.getElementById("potree_render_area"));
    window.viewer = viewer;
    if (!viewer.renderer) throw new Error("Przeglądarka nie mogła uruchomić widoku 3D.");
    viewer.setEDLEnabled(true);
    viewer.setFOV(60);
    viewer.setPointBudget(500_000);
    viewer.setBackground("gradient");
    viewer.setControls(viewer.orbitControls);
    viewer.setCameraMode(Potree.CameraMode.PERSPECTIVE);
    const canvas = viewer.renderer.domElement;
    canvas.tabIndex = 0;
    canvas.setAttribute("aria-label", "Widok 3D. Przeciągnij, aby obrócić; kółko myszy przybliża widok.");
    for (const type of ["pointerdown", "touchstart", "wheel", "keydown"]) {
      canvas.addEventListener(type, () => notify({type: "interact"}), {capture: true, passive: true});
    }
    canvas.addEventListener("pointerdown", () => canvas.focus({preventScroll: true}));
    canvas.addEventListener("webglcontextlost", () => showError("Utracono widok 3D. Spróbuj wczytać porównanie ponownie."));
    const event = await Promise.race([
      Potree.loadPointCloud(cloud.url, cloud.name),
      new Promise((resolve, reject) => {
        timeout = setTimeout(() => reject(new Error("Nie udało się wczytać chmury. Spróbuj ponownie.")), 45_000);
      }),
    ]);
    const pointcloud = event.pointcloud;
    pointcloud.material.size = 1;
    pointcloud.material.pointSizeType = Potree.PointSizeType.ADAPTIVE;
    pointcloud.material.shape = Potree.PointShape.SQUARE;
    viewer.scene.addPointCloud(pointcloud);
    const box = viewer.getBoundingBox([pointcloud]);
    const bounds = {min: box.min.toArray(), max: box.max.toArray()};
    ready = true;
    fitBounds(bounds);
    status.hidden = true;
    viewer.addEventListener("update", publishCamera);
    notify({type: "ready", bounds, camera: readCamera(viewer)});
  } catch (error) {
    showError(error.message);
  } finally {
    clearTimeout(timeout);
  }
}

initialize();
