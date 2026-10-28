import {api, json, element} from "./common.js";
import {captureProjectScene, clearProjectTools, restoreProjectScene} from "./project-scene.js";
import {createProjectSharing} from "./project-sharing.js";

const nextFrame = () => new Promise(resolve => requestAnimationFrame(resolve));

export function createProjectPanel({viewer, getCloud, getPointcloud, loadCloud, stopEditing, afterRestore, hasPendingSelections, isCollectionView = () => false}) {
  const byId = id => document.getElementById(id);
  const panel = byId("project_panel");
  const name = byId("project_name");
  const list = byId("project_list");
  const status = byId("project_status");
  const controls = [...panel.querySelectorAll("button,input,select")];
  let current = null;
  let baseline = null;
  let baselineName = "";
  let busy = false;
  let opening = false;
  let listRevision = 0;
  const sharing = createProjectSharing({async onChanged(updated) {
    if (current?.id === updated.id) current = {...current, ...updated};
    await refreshList();
    updateControls();
  }});

  function message(text, kind = "info") {
    status.textContent = text;
    status.dataset.kind = kind;
  }

  function snapshot() { return captureProjectScene(viewer, getPointcloud()); }

  function hasChanges() {
    if (isCollectionView()) return false;
    if (!getPointcloud() || !getCloud()?.canEdit || (current && !current.canEdit)) return false;
    if (hasPendingSelections()) return true;
    const scene = viewer.scene;
    if (!current && !name.value.trim() && !scene.measurements.length && !scene.volumes.length
      && !scene.profiles.length && !scene.annotations.children.length) return false;
    return baseline !== null &&
      (JSON.stringify(snapshot()) !== baseline || name.value.trim() !== baselineName);
  }

  function allowReplacement() {
    return !hasChanges() || window.confirm("Niezapisane zmiany w widoku zostaną zastąpione. Kontynuować?");
  }

  function updateControls() {
    const editable = !isCollectionView() && getCloud()?.canEdit && (!current || current.canEdit);
    panel.hidden = isCollectionView() || (!editable && !current);
    byId("project_owner_controls").hidden = !editable;
    byId("project_access").textContent = current
      ? `${current.name} · ${current.visibility === "public" ? "Publiczny" : "Prywatny"}${current.canEdit ? "" : " · Tylko odczyt"}` : "";
    for (const control of controls) control.disabled = busy;
    for (const control of byId("project_owner_controls").querySelectorAll("button,input,select")) control.disabled = busy || !editable;
    byId("project_share").hidden = !current;
    byId("project_share").textContent = current?.canEdit ? "Udostępnij" : "Link do projektu";
    byId("project_save").textContent = current ? "Zapisz zmiany" : "Zapisz projekt";
    byId("project_copy").disabled = busy || !current || !editable;
    byId("project_open").disabled = busy || !list.value || !editable;
    byId("project_delete").disabled = busy || !list.value || !editable;
  }

  function setUrl() {
    const url = current ? `/viewer?project=${encodeURIComponent(current.id)}`
      : `/viewer?cloud=${encodeURIComponent(getCloud().id)}`;
    window.history.replaceState(null, "", url);
    document.title = `${current?.name || getCloud().name} · CloudPoint`;
  }

  async function refreshList() {
    const cloud = getCloud();
    if (!cloud?.canEdit) return;
    const revision = ++listRevision;
    const selected = current?.id || list.value;
    const {projects} = await api(`/api/projects?cloudId=${encodeURIComponent(cloud.id)}`);
    if (revision !== listRevision || getCloud()?.id !== cloud.id) return;
    list.replaceChildren();
    if (!projects.length) list.append(element("option", "", "Brak zapisanych projektów"));
    for (const project of projects) {
      const option = element("option", "", project.name);
      option.value = project.id;
      list.append(option);
    }
    if (!projects.length) list.firstElementChild.value = "";
    else if (projects.some(project => project.id === selected)) list.value = selected;
    updateControls();
  }

  async function cloudChanged() {
    listRevision++;
    current = null;
    baseline = null;
    name.value = "";
    baselineName = "";
    list.replaceChildren();
    updateControls();
    if (panel.hidden) return;
    await nextFrame();
    await nextFrame();
    baseline = JSON.stringify(snapshot());
    message("Projekt zapisuje się na Twoim koncie.");
    await refreshList();
  }

  async function save(copy = false) {
    if (busy || isCollectionView() || !getCloud()?.canEdit || (current && !current.canEdit)) return;
    if (!name.value.trim()) { message("Podaj nazwę projektu.", "error"); name.focus(); return; }
    stopEditing();
    const state = snapshot();
    const serialized = JSON.stringify(state);
    const savedName = copy && name.value.trim() === current?.name ? `${[...name.value.trim()].slice(0, 110).join("")} — kopia` : name.value.trim();
    busy = true;
    updateControls();
    message("Zapisywanie projektu…");
    try {
      const updating = current && !copy;
      const body = {name: savedName, sceneState: state,
        ...(updating ? {revision: current.revision} : {cloudId: getCloud().id})};
      current = await api(updating ? `/api/projects/${current.id}` : "/api/projects", json(updating ? "PUT" : "POST", body));
      name.value = current.name;
      baselineName = current.name;
      baseline = serialized;
      setUrl();
      await refreshList();
      message("Projekt został zapisany.");
    } catch (error) {
      message(error.message, "error");
    } finally { busy = false; updateControls(); }
  }

  async function open(projectOrId, initial = false) {
    if (busy || (!initial && !allowReplacement())) return;
    busy = true;
    opening = true;
    updateControls();
    message("Otwieranie projektu…");
    try {
      const project = typeof projectOrId === "string" ? await api(`/api/projects/${encodeURIComponent(projectOrId)}`) : projectOrId;
      stopEditing();
      await loadCloud(project.cloudId, project);
      await nextFrame();
      await nextFrame();
      await viewer.promiseGuiLoaded();
      restoreProjectScene(viewer, getPointcloud(), project.sceneState);
      current = project;
      name.value = project.name;
      baselineName = project.name;
      baseline = JSON.stringify(snapshot());
      afterRestore();
      setUrl();
      await refreshList();
      panel.open = true;
      message(project.canEdit ? "Projekt został otwarty." : "Projekt publiczny — zmiany widoku są lokalne i nie nadpisują zapisu właściciela.");
    } catch (error) {
      message(error.message, "error");
      throw error;
    } finally { busy = false; opening = false; updateControls(); }
  }

  byId("project_save").addEventListener("click", () => save());
  byId("project_copy").addEventListener("click", () => save(true));
  byId("project_share").addEventListener("click", () => { if (!busy && current) sharing.open(current); });
  byId("project_open").addEventListener("click", () => open(list.value).catch(() => {}));
  list.addEventListener("change", updateControls);
  name.addEventListener("keydown", event => {
    if (event.key === "Enter") { event.preventDefault(); save(); }
  });
  byId("project_delete").addEventListener("click", async () => {
    if (busy || !getCloud()?.canEdit || !list.value || !window.confirm("Usunąć wybrany zapis projektu?")) return;
    busy = true;
    updateControls();
    try {
      await api(`/api/projects/${encodeURIComponent(list.value)}`, {method: "DELETE"});
      if (current?.id === list.value) { current = null; setUrl(); }
      await refreshList();
      message("Zapis projektu został usunięty.");
    } catch (error) { message(error.message, "error"); }
    finally { busy = false; updateControls(); }
  });
  window.addEventListener("beforeunload", event => {
    if (hasChanges()) { event.preventDefault(); event.returnValue = ""; }
  });

  return {
    cloudChanged, open,
    allowCloudChange(url) {
      if (opening) return true;
      if (busy) { message("Poczekaj na zakończenie zapisywania projektu."); return false; }
      if (!allowReplacement()) return false;
      stopEditing();
      clearProjectTools(viewer);
      return true;
    },
  };
}
