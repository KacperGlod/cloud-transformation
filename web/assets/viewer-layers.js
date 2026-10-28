import {api, element, statusLabel} from "./common.js";

export function createViewerLayers({getClouds, loadPointcloud, beforeOpen, onOpen, onActive, onFit, onLeave, preferredCloudId}) {
  const byId = id => document.getElementById(id);
  const panel = byId("viewer_layers"), select = byId("layer_collection");
  const list = byId("layer_list"), status = byId("layer_status");
  let collection = null, layers = [], activeId = null, generation = 0, busy = false, autoFit = true;
  const cleanups = [];
  for (const type of ["pointerdown", "wheel", "keydown"]) {
    document.addEventListener(type, () => { if (busy) autoFit = false; }, {passive: true});
  }

  function controls() {
    byId("layer_load").disabled = busy || !select.value;
    byId("layer_fit").disabled = !layers.some(layer => layer.pointcloud?.visible);
    byId("layer_single").disabled = !activeId;
    list.setAttribute("aria-busy", String(busy));
  }

  function sync(layer) {
    const {checkbox, range, output, activate, detail} = layer.ui;
    checkbox.checked = layer.visible;
    checkbox.disabled = !layer.pointcloud;
    range.value = layer.transparency;
    range.disabled = !layer.pointcloud;
    output.value = `${layer.transparency}%`;
    range.setAttribute("aria-valuetext", `${layer.transparency}% przezroczystości`);
    activate.disabled = !layer.pointcloud;
    activate.textContent = activeId === layer.cloud.id ? "Aktywna" : "Ustaw aktywną";
    activate.setAttribute("aria-pressed", String(activeId === layer.cloud.id));
    layer.ui.row.classList.toggle("is-active", activeId === layer.cloud.id);
    detail.textContent = layer.error || (layer.pointcloud ? "" : layer.cloud.status === "ready" ? "Wczytywanie…" : statusLabel(layer.cloud.status));
    detail.dataset.kind = layer.error ? "error" : "info";
    controls();
  }

  function apply(layer) {
    layer.applying = true;
    layer.pointcloud.material.opacity = 1 - layer.transparency / 100;
    layer.pointcloud.visible = layer.visible && layer.transparency < 100;
    layer.applying = false;
    sync(layer);
  }

  function activate(layer) {
    if (!layer.pointcloud) return;
    activeId = layer.cloud.id;
    onActive(layer.cloud, layer.pointcloud, collection);
    for (const item of layers) sync(item);
  }

  function render() {
    list.replaceChildren();
    for (const layer of layers) {
      const row = element("li", "viewer-layer");
      row.dataset.cloudId = layer.cloud.id;
      const heading = element("div", "viewer-layer-heading");
      const label = element("label", "viewer-layer-name");
      const checkbox = element("input");
      checkbox.type = "checkbox";
      checkbox.setAttribute("aria-label", `Widoczność: ${layer.cloud.name}`);
      label.append(checkbox, element("span", "", layer.cloud.name));
      const button = element("button", "secondary-button layer-activate");
      button.type = "button";
      button.setAttribute("aria-label", `Aktywna chmura: ${layer.cloud.name}`);
      heading.append(label, button);
      const opacity = element("div", "viewer-layer-opacity");
      const rangeLabel = element("label", "", "Przezroczystość");
      const range = element("input");
      range.type = "range"; range.min = "0"; range.max = "100"; range.step = "1";
      range.id = `layer_opacity_${layer.cloud.id}`;
      rangeLabel.htmlFor = range.id;
      const output = element("output");
      output.htmlFor = range.id;
      opacity.append(rangeLabel, output, range);
      const detail = element("p", "viewer-layer-detail");
      opacity.hidden = button.hidden = layer.cloud.status !== "ready";
      row.append(heading, opacity, detail);
      list.append(row);
      layer.ui = {row, checkbox, range, output, activate: button, detail};
      checkbox.addEventListener("change", () => { layer.visible = checkbox.checked; apply(layer); });
      range.addEventListener("input", () => { layer.transparency = Number(range.value); apply(layer); });
      button.addEventListener("click", () => activate(layer));
      sync(layer);
    }
  }

  function release() {
    for (const cleanup of cleanups.splice(0)) cleanup();
    for (const layer of layers) {
      if (!layer.pointcloud) continue;
      layer.pointcloud.visible = false;
      layer.pointcloud.material.opacity = layer.previousOpacity;
    }
    layers = [];
    activeId = null;
  }

  async function open(identifier) {
    if (busy) return false;
    busy = true; controls();
    status.textContent = "Wczytywanie kolekcji…";
    status.dataset.kind = "info";
    const version = ++generation;
    try {
      const selected = await api(`/api/collections/${encodeURIComponent(identifier)}`);
      if (version !== generation) return false;
      const clouds = new Map((await getClouds()).map(cloud => [cloud.id, cloud]));
      if (version !== generation) return false;
      const members = selected.cloudIds.map(id => clouds.get(id)).filter(Boolean)
        .sort((a, b) => a.name.localeCompare(b.name, "pl", {numeric: true, sensitivity: "base"}));
      if (!members.some(cloud => cloud.status === "ready" && cloud.url)) throw new Error("Kolekcja nie zawiera gotowych chmur do wyświetlenia.");
      if (!beforeOpen()) { status.textContent = ""; return false; }
      release();
      autoFit = true;
      collection = selected;
      layers = members.map(cloud => ({cloud, visible: cloud.status === "ready", transparency: 0, pointcloud: null, error: ""}));
      onOpen(collection);
      byId("layer_current").textContent = collection.name;
      byId("layer_content").hidden = false;
      byId("layer_library").href = `/?collection=${encodeURIComponent(collection.id)}`;
      select.value = identifier;
      panel.open = true;
      render();
      let completed = 0;
      const ready = layers.filter(layer => layer.cloud.status === "ready" && layer.cloud.url);
      // Load metadata in sequence; Potree's global point budget is shared by all layers.
      const preferred = ready.find(layer => layer.cloud.id === preferredCloudId);
      const queue = preferred ? [preferred, ...ready.filter(layer => layer !== preferred)] : ready;
      for (const layer of queue) {
        if (version !== generation) return false;
        status.textContent = `Wczytywanie warstw: ${completed + 1} z ${queue.length}…`;
        try {
          const pointcloud = await loadPointcloud(layer.cloud);
          if (version !== generation) return false;
          layer.pointcloud = pointcloud;
          layer.previousOpacity = pointcloud.material.opacity;
          apply(layer);
          const visibilityChanged = () => {
            if (layer.applying) return;
            layer.visible = pointcloud.visible;
            apply(layer);
          };
          const opacityChanged = () => {
            if (layer.applying) return;
            layer.transparency = Math.round((1 - pointcloud.material.opacity) * 100);
            apply(layer);
          };
          pointcloud.addEventListener("visibility_changed", visibilityChanged);
          pointcloud.material.addEventListener("opacity_changed", opacityChanged);
          cleanups.push(() => {
            pointcloud.removeEventListener("visibility_changed", visibilityChanged);
            pointcloud.material.removeEventListener("opacity_changed", opacityChanged);
          });
          if (!activeId) { activate(layer); if (autoFit) onFit([pointcloud]); }
        } catch (error) {
          if (version !== generation) return false;
          console.warn("Could not load collection layer", layer.cloud.id, error);
          layer.error = "Nie udało się wczytać tej chmury. Spróbuj ponownie.";
          sync(layer);
        }
        completed++;
      }
      const failed = layers.filter(layer => layer.error).length;
      const skipped = layers.filter(layer => layer.cloud.status !== "ready").length;
      if (autoFit) onFit(layers.map(layer => layer.pointcloud).filter(pointcloud => pointcloud?.visible));
      status.textContent = `Wczytano warstw: ${layers.filter(layer => layer.pointcloud).length}.${skipped ? ` Niegotowe: ${skipped}.` : ""}${failed ? ` Błędy: ${failed}. Kliknij „Wczytaj”, aby ponowić.` : ""}`;
      status.dataset.kind = failed ? "error" : "info";
      return true;
    } catch (error) {
      if (version === generation) { status.textContent = error.message; status.dataset.kind = "error"; }
      return false;
    } finally {
      if (version === generation) { busy = false; controls(); }
    }
  }

  select.addEventListener("change", controls);
  byId("layer_load").addEventListener("click", () => open(select.value));
  byId("layer_fit").addEventListener("click", () => onFit(layers.map(layer => layer.pointcloud).filter(pointcloud => pointcloud?.visible)));
  byId("layer_single").addEventListener("click", () => {
    const layer = layers.find(item => item.cloud.id === activeId);
    if (layer) onLeave(layer.cloud);
  });

  return {
    open,
    async refresh(signedIn) {
      panel.hidden = !signedIn;
      if (!signedIn) return;
      try {
        const {collections} = await api("/api/collections");
        const selected = select.value;
        select.replaceChildren();
        const placeholder = element("option", "", collections.length ? "Wybierz kolekcję…" : "Brak kolekcji");
        placeholder.value = ""; select.append(placeholder);
        for (const item of collections) {
          const option = element("option", "", item.name);
          option.value = item.id; select.append(option);
        }
        if (collections.some(item => item.id === selected)) select.value = selected;
        controls();
      } catch (error) { status.textContent = error.message; status.dataset.kind = "error"; }
    },
    leave() {
      generation++;
      busy = false;
      release();
      collection = null;
      byId("layer_content").hidden = true;
      status.textContent = "";
      controls();
    },
  };
}
