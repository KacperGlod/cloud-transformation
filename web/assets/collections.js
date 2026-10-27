import {api, element, json, statusLabel} from "./common.js";

export function createCollections({onFilterChange, onChanged, onError}) {
  const byId = id => document.getElementById(id);
  const filter = byId("collection_filter");
  const dialog = byId("collection_dialog");
  const createDialog = byId("collection_create_dialog");
  let collections = [], clouds = [], suggestions = [];
  let current = null, mutating = false, creating = false, loading = false;
  let controller, requestVersion = 0, editorSignature = "";
  const selected = new Set();
  const editableFields = ["name", "description", "notes"];
  let requestedFilter = new URLSearchParams(window.location.search).get("collection");

  function syncFilterUrl() {
    const url = new URL(window.location.href);
    if (filter.value === "all") url.searchParams.delete("collection");
    else url.searchParams.set("collection", filter.value);
    if (url.href !== window.location.href) window.history.replaceState(window.history.state, "", url);
  }

  function renderToolbar() {
    const previous = filter.value;
    filter.replaceChildren();
    for (const [id, label] of [["all", "Wszystkie chmury"], ["unassigned", "Bez kolekcji"],
      ...collections.map(item => [item.id, `${item.name} (${item.cloudIds.length})`])]) {
      const option = element("option", "", label);
      option.value = id;
      filter.append(option);
    }
    filter.value = [...filter.options].some(option => option.value === previous) ? previous : "all";
    byId("collection_manage").disabled = !collections.some(item => item.id === filter.value);
  }

  function updateControls() {
    for (const control of dialog.querySelectorAll("button,input,textarea")) {
      const editable = editableFields.some(field => control.id === `collection_${field}`);
      control.disabled = mutating || (loading && !editable && !control.hasAttribute("data-collection-close") && control.id !== "collection_refresh");
    }
    byId("collection_save").disabled = mutating || loading || !editableFields.some(field =>
      byId(`collection_${field}`).value !== (current?.[field] || ""));
    byId("collection_add").disabled = mutating || loading || !selected.size;
    byId("collection_select_suggestions").disabled = mutating || loading || !suggestions.length;
    byId("collection_selected_count").textContent = selected.size ? `Wybrano chmur: ${selected.size}` : "Nie wybrano chmur";
    byId("collection_content").setAttribute("aria-busy", String(loading || mutating));
  }

  function syncDraft(collection, reset = false, savedFields = []) {
    for (const field of editableFields) {
      const input = byId(`collection_${field}`);
      if (reset || savedFields.includes(field) || input.value === (current?.[field] || "")) input.value = collection[field] || "";
    }
  }

  function renderLists() {
    if (!current) return;
    const members = new Set(current.cloudIds);
    const focusedId = document.activeElement?.dataset.collectionChoice;
    const known = new Map(clouds.map(cloud => [cloud.id, cloud]));
    for (const id of selected) if (!known.has(id) || members.has(id)) selected.delete(id);
    const memberList = byId("collection_members");
    memberList.replaceChildren();
    const compare = (a, b) => a.name.localeCompare(b.name, "pl", {numeric: true, sensitivity: "base"});
    const visibleMembers = clouds.filter(cloud => members.has(cloud.id)).sort(compare);
    byId("collection_member_count").textContent = visibleMembers.length;
    if (!visibleMembers.length) memberList.append(element("li", "collection-empty", "Kolekcja jest pusta. Wybierz poniżej pierwsze chmury."));
    for (const cloud of visibleMembers) {
      const row = element("li", "collection-member");
      const description = element("div");
      description.append(element("strong", "", cloud.name), element("small", "", statusLabel(cloud.status)));
      const remove = element("button", "secondary", "Odłącz");
      remove.type = "button";
      remove.setAttribute("aria-label", `Usuń chmurę ${cloud.name} z kolekcji`);
      remove.addEventListener("click", () => mutate(() => api(`/api/collections/${current.id}/clouds/${cloud.id}`, {method: "DELETE"})));
      row.append(description, remove);
      memberList.append(row);
    }
    const query = byId("collection_search").value.toLocaleLowerCase("pl");
    const reasons = new Map(suggestions.map(item => [item.cloudId, item]));
    const proposed = byId("collection_suggestions"), other = byId("collection_other_clouds");
    proposed.replaceChildren(); other.replaceChildren();
    const available = clouds.filter(cloud => !members.has(cloud.id) && cloud.name.toLocaleLowerCase("pl").includes(query));
    available.sort((a, b) => (reasons.get(a.id)?.distance || 0) - (reasons.get(b.id)?.distance || 0) || compare(a, b));
    for (const cloud of available) {
      const row = element("li");
      const label = element("label", "collection-choice");
      const checkbox = element("input");
      checkbox.type = "checkbox";
      checkbox.dataset.collectionChoice = cloud.id;
      checkbox.checked = selected.has(cloud.id);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked && selected.size >= 1000) {
          checkbox.checked = false;
          byId("collection_status").textContent = "Jednorazowo można dodać do 1000 chmur.";
          return;
        }
        if (checkbox.checked) selected.add(cloud.id);
        else selected.delete(cloud.id);
        updateControls();
      });
      const description = element("span");
      description.append(element("strong", "", cloud.name));
      const reason = reasons.get(cloud.id);
      description.append(element("small", "", reason ? `Powiązana z: ${reason.relatedCloudName} · ${statusLabel(cloud.status)}` : statusLabel(cloud.status)));
      label.append(checkbox, description); row.append(label);
      (reason ? proposed : other).append(row);
    }
    if (!proposed.childElementCount) proposed.append(element("li", "collection-empty", query ? "Brak pasujących propozycji." : members.size ? "Nie ma kolejnych powiązanych chmur do dodania." : "Dodaj pierwszą chmurę, aby zobaczyć powiązane propozycje."));
    if (!other.childElementCount) other.append(element("li", "collection-empty", query ? "Brak pasujących chmur." : "Brak pozostałych chmur do dodania."));
    updateControls();
    if (focusedId) dialog.querySelector(`[data-collection-choice="${CSS.escape(focusedId)}"]`)?.focus({preventScroll: true});
  }

  async function loadEditor() {
    controller?.abort();
    controller = new AbortController();
    const version = ++requestVersion, id = current.id;
    loading = true;
    byId("collection_status").textContent = "Wczytywanie kolekcji…";
    updateControls();
    try {
      const [collection, result] = await Promise.all([
        api(`/api/collections/${id}`, {signal: controller.signal}),
        api(`/api/collections/${id}/suggestions`, {signal: controller.signal}),
      ]);
      if (version !== requestVersion || !dialog.open) return;
      syncDraft(collection);
      current = collection;
      suggestions = result.suggestions;
      byId("collection_status").textContent = "";
      renderLists();
    } catch (error) {
      if (version === requestVersion && error.name !== "AbortError") byId("collection_status").textContent = error.message;
    } finally {
      if (version === requestVersion) { loading = false; updateControls(); }
    }
  }

  function openEditor(collection) {
    current = collection;
    suggestions = [];
    selected.clear();
    byId("collection_search").value = "";
    syncDraft(collection, true);
    dialog.showModal();
    renderLists();
    loadEditor();
  }

  async function mutate(action, savedFields = []) {
    if (mutating || loading) return;
    mutating = true;
    byId("collection_status").textContent = "Zapisywanie…";
    updateControls();
    try {
      const updated = await action();
      syncDraft(updated, false, savedFields);
      current = updated;
      collections = collections.map(item => item.id === current.id ? current : item);
      renderToolbar();
      await onChanged();
      await loadEditor();
    } catch (error) { byId("collection_status").textContent = error.message; }
    finally { mutating = false; updateControls(); }
  }

  filter.addEventListener("change", () => { renderToolbar(); syncFilterUrl(); onFilterChange(); });
  byId("collection_manage").addEventListener("click", () => {
    const collection = collections.find(item => item.id === filter.value);
    if (collection) openEditor(collection);
  });
  byId("collection_create").addEventListener("click", () => {
    byId("collection_create_form").reset();
    byId("collection_create_status").textContent = "";
    createDialog.showModal();
  });
  byId("collection_create_form").addEventListener("submit", async event => {
    event.preventDefault();
    if (creating) return;
    creating = true;
    for (const control of event.target.elements) control.disabled = true;
    try {
      const collection = await api("/api/collections", json("POST", {
        name: byId("collection_create_name").value,
        description: byId("collection_create_description").value,
        notes: byId("collection_create_notes").value,
      }));
      collections.push(collection);
      renderToolbar(); filter.value = collection.id; renderToolbar();
      syncFilterUrl();
      createDialog.close();
      await onChanged();
      openEditor(collection);
    } catch (error) { byId("collection_create_status").textContent = error.message; }
    finally {
      creating = false;
      for (const control of event.target.elements) control.disabled = false;
    }
  });
  byId("collection_details_form").addEventListener("submit", event => {
    event.preventDefault();
    const changes = Object.fromEntries(editableFields.filter(field =>
      byId(`collection_${field}`).value !== (current[field] || "")).map(field => [field, byId(`collection_${field}`).value]));
    if (Object.keys(changes).length) mutate(() => api(`/api/collections/${current.id}`, json("PATCH", changes)), Object.keys(changes));
  });
  for (const field of editableFields) byId(`collection_${field}`).addEventListener("input", updateControls);
  byId("collection_add_form").addEventListener("submit", event => {
    event.preventDefault();
    if (selected.size) mutate(() => api(`/api/collections/${current.id}/clouds`, json("POST", {cloudIds: [...selected]})));
  });
  byId("collection_search").addEventListener("input", renderLists);
  byId("collection_refresh").addEventListener("click", () => { if (!mutating) loadEditor(); });
  byId("collection_select_suggestions").addEventListener("click", () => {
    const known = new Set(clouds.map(cloud => cloud.id));
    const ids = suggestions.map(item => item.cloudId).filter(id => known.has(id) && !current.cloudIds.includes(id));
    if (new Set([...selected, ...ids]).size > 1000) {
      byId("collection_status").textContent = "Jednorazowo można dodać do 1000 chmur. Wybierz mniejszą grupę.";
      return;
    }
    for (const id of ids) selected.add(id);
    renderLists();
  });
  byId("collection_delete").addEventListener("click", async () => {
    if (mutating || loading || !window.confirm(`Usunąć kolekcję „${current.name}”? Chmury pozostaną w bibliotece.`)) return;
    mutating = true; updateControls();
    try {
      await api(`/api/collections/${current.id}`, {method: "DELETE"});
      collections = collections.filter(item => item.id !== current.id);
      dialog.close(); renderToolbar(); onFilterChange();
      syncFilterUrl();
      await onChanged();
    } catch (error) { byId("collection_status").textContent = error.message; }
    finally { mutating = false; updateControls(); }
  });
  for (const panel of [dialog, createDialog]) {
    for (const close of panel.querySelectorAll("[data-collection-close]")) close.addEventListener("click", () => {
      if (!(panel === dialog ? mutating : creating)) panel.close();
    });
    panel.addEventListener("cancel", event => { if (panel === dialog ? mutating : creating) event.preventDefault(); });
  }
  function isBackdropEvent(event) {
    if (event.target !== dialog) return false;
    const bounds = dialog.getBoundingClientRect();
    return event.clientX < bounds.left || event.clientX >= bounds.right ||
      event.clientY < bounds.top || event.clientY >= bounds.bottom;
  }
  dialog.addEventListener("mousedown", event => {
    // Backdrop presses otherwise start text selection inside the modal.
    if (isBackdropEvent(event)) event.preventDefault();
  });
  dialog.addEventListener("dblclick", event => {
    if (!mutating && isBackdropEvent(event)) dialog.close();
  });
  dialog.addEventListener("close", () => { controller?.abort(); requestVersion++; loading = false; });

  return {
    update(data, cloudData) {
      collections = data;
      clouds = cloudData;
      renderToolbar();
      if (requestedFilter !== null) {
        const requested = requestedFilter;
        requestedFilter = null;
        if (requested === "unassigned" || data.some(item => item.id === requested)) {
          filter.value = requested;
          renderToolbar();
        } else if (requested !== "all") onError(new Error("Kolekcja jest niedostępna. Pokazano wszystkie chmury."));
      }
      syncFilterUrl();
      const signature = JSON.stringify([data, cloudData.map(cloud => [cloud.id, cloud.name, cloud.status, cloud.parentCloud])]);
      if (dialog.open && !mutating) {
        if (!data.some(item => item.id === current.id)) {
          dialog.close(); onError(new Error("Kolekcja została usunięta."));
        } else if (signature !== editorSignature) loadEditor();
      }
      editorSignature = signature;
    },
    includes(cloudId) {
      if (filter.value === "all") return true;
      if (filter.value === "unassigned") return !collections.some(item => item.cloudIds.includes(cloudId));
      return collections.find(item => item.id === filter.value)?.cloudIds.includes(cloudId) || false;
    },
    isFiltered: () => filter.value !== "all",
    currentCollection: () => collections.find(item => item.id === filter.value) || null,
    select(identifier) {
      if (!collections.some(item => item.id === identifier)) return false;
      requestedFilter = null;
      filter.value = identifier;
      renderToolbar();
      syncFilterUrl();
      onFilterChange();
      return true;
    },
  };
}
