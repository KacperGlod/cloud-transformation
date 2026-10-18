import {api} from "./common.js";
import {ComparisonController} from "./comparison-camera.js";

const byId = id => document.getElementById(id);
const frames = [byId("source_frame"), byId("result_frame")];
const sync = byId("sync_cameras");
const fit = byId("fit_comparison");
const retry = byId("retry_comparison");
const status = byId("comparison_status");
let controller;
let loadTimeout;
let failed = false;

function send(index, message) {
  frames[index].contentWindow.postMessage({channel: "cloudpoint-comparison", ...message}, window.location.origin);
}

function showError(message) {
  failed = true;
  clearTimeout(loadTimeout);
  status.textContent = message;
  status.dataset.kind = "error";
  retry.hidden = false;
  sync.disabled = true;
  fit.disabled = true;
}

function showReady() {
  status.dataset.kind = "info";
  status.textContent = sync.checked ? "Wspólna kamera dla obu chmur." : "Każdym widokiem sterujesz osobno.";
}

window.addEventListener("message", event => {
  if (failed || event.origin !== window.location.origin || event.data?.channel !== "cloudpoint-comparison") return;
  const index = frames.findIndex(frame => frame.contentWindow === event.source);
  if (index < 0 || !controller) return;
  if (event.data.type === "error") {
    showError(`${index === 0 ? "Chmura źródłowa" : "Wynik"}: ${event.data.message}`);
    return;
  }
  controller.receive(index, event.data);
  if (event.data.type === "ready" && controller.ready) {
    clearTimeout(loadTimeout);
    sync.disabled = false;
    fit.disabled = false;
    showReady();
  }
});

sync.addEventListener("change", () => {
  controller.setSynchronized(sync.checked);
  showReady();
});
fit.addEventListener("click", () => controller.fit());
retry.addEventListener("click", () => window.location.reload());

async function initialize() {
  try {
    const identifier = new URLSearchParams(window.location.search).get("cloud");
    if (!identifier) throw new Error("Otwórz porównanie przyciskiem przy chmurze wynikowej w bibliotece.");
    const {source, result} = await api(`/api/clouds/${encodeURIComponent(identifier)}/comparison`);
    byId("source_heading").textContent = source.name;
    byId("result_heading").textContent = result.name;
    byId("result_link").href = result.viewerUrl;
    byId("result_link").hidden = false;
    document.title = `${result.name} · Porównanie · CloudPoint`;
    controller = new ComparisonController(send);
    byId("comparison_panes").hidden = false;
    [source, result].forEach((cloud, index) => {
      frames[index].src = `/compare/view?cloud=${encodeURIComponent(cloud.id)}`;
    });
    loadTimeout = setTimeout(() => showError("Przekroczono czas wczytywania. Sprawdź połączenie i spróbuj ponownie."), 60_000);
  } catch (error) {
    showError(error.message);
  }
}

initialize();
