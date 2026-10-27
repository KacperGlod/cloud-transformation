import {api, element, json} from "./common.js";

const MAX_SELECTION = 1000;

export function createLibrarySelection({onChanged, onMessage}) {
  const byId = id => document.getElementById(id);
  const dialog = byId("collection_bulk_dialog");
  const target = byId("collection_bulk_target");
  const selectAll = byId("library_select_all");
  const selected = new Set();
  let clouds = [], visible = [], collections = [], currentCollection = null;
  let scope, busy = false, mode = "add", batchIds = [], removeCollectionId = null;
  let targetSignature;

  function renderSelection() {
    const count = selected.size;
    byId("library_bulk_actions").hidden = !count;
    byId("library_selected_count").textContent = `Wybrano chmur: ${count}`;
    byId("library_bulk_remove").hidden = !currentCollection;
    for (const id of ["library_bulk_add", "library_bulk_remove", "library_clear_selection"]) byId(id).disabled = busy;
    selectAll.checked = visible.length > 0 && count === visible.length;
    selectAll.indeterminate = count > 0 && count < visible.length;
    selectAll.disabled = busy || !visible.length;
    for (const checkbox of byId("cloud_rows").querySelectorAll("[data-library-cloud]")) {
      checkbox.checked = selected.has(checkbox.dataset.libraryCloud);
      checkbox.disabled = busy;
      checkbox.closest("tr").classList.toggle("cloud-selected", checkbox.checked);
    }
  }

  function renderTargets() {
    const signature = JSON.stringify(collections.map(item => [item.id, item.name]));
    if (signature === targetSignature) return;
    targetSignature = signature;
    const previous = target.value;
    target.replaceChildren();
    for (const [value, text] of [["", "Wybierz kolekcję…"], ...collections.map(item => [item.id, item.name]), ["new", "＋ Nowa kolekcja…"]]) {
      const option = element("option", "", text);
      option.value = value;
      target.append(option);
    }
    target.value = [...target.options].some(option => option.value === previous) ? previous : "";
  }

  function renderDialog() {
    if (!dialog.open) return;
    renderTargets();
    const removing = mode === "remove";
    const creating = !removing && target.value === "new";
    const collection = collections.find(item => item.id === (removing ? removeCollectionId : target.value));
    const missingClouds = batchIds.some(id => !clouds.some(cloud => cloud.id === id));
    const alreadyAdded = collection ? batchIds.filter(id => collection.cloudIds.includes(id)).length : 0;
    byId("collection_bulk_title").textContent = removing ? "Odłącz chmury od kolekcji" : "Dodaj chmury do kolekcji";
    byId("collection_bulk_summary").textContent = removing
      ? `Wybrano chmur: ${batchIds.length}. Kolekcja: „${collection?.name || "Niedostępna"}”.`
      : `Wybrano chmur: ${batchIds.length}. Wybierz kolekcję lub utwórz nową.`;
    byId("collection_bulk_target_field").hidden = removing;
    byId("collection_bulk_name_field").hidden = !creating;
    byId("collection_bulk_name").required = creating;
    const hint = byId("collection_bulk_hint");
    hint.textContent = missingClouds ? "Część chmur jest już niedostępna. Zamknij okno i wybierz chmury ponownie."
      : removing ? (collection ? "Chmury pozostaną w bibliotece i w pozostałych kolekcjach." : "Kolekcja jest już niedostępna.")
      : creating ? "Nowa kolekcja od razu otrzyma zaznaczone chmury."
      : collection ? `Do dodania: ${batchIds.length - alreadyAdded}. Już w kolekcji: ${alreadyAdded}.`
      : "Chmura może należeć do kilku kolekcji.";
    for (const control of dialog.querySelectorAll("button,input,select")) control.disabled = busy;
    const submit = byId("collection_bulk_submit");
    submit.textContent = busy ? "Zapisywanie…" : removing ? "Odłącz chmury" : creating ? "Utwórz i dodaj chmury" : "Dodaj chmury";
    submit.disabled = busy || missingClouds || (!creating && !collection) || (!removing && !creating && alreadyAdded === batchIds.length);
  }

  function open(action) {
    if (busy || !selected.size || (action === "remove" && !currentCollection)) return;
    mode = action;
    batchIds = [...selected];
    removeCollectionId = currentCollection?.id;
    renderTargets();
    target.value = collections.length ? "" : "new";
    byId("collection_bulk_name").value = "";
    byId("collection_bulk_status").textContent = "";
    dialog.showModal();
    renderDialog();
    (action === "remove" ? byId("collection_bulk_submit") : target).focus();
  }

  selectAll.addEventListener("change", () => {
    if (selectAll.checked && visible.length > MAX_SELECTION) {
      onMessage(`Jednorazowo można zaznaczyć do ${MAX_SELECTION} chmur. Zawęź filtr lub wybierz je pojedynczo.`);
    } else {
      selected.clear();
      if (selectAll.checked) for (const cloud of visible) selected.add(cloud.id);
    }
    renderSelection();
  });
  byId("library_clear_selection").addEventListener("click", () => {
    if (busy) return;
    selected.clear(); renderSelection(); selectAll.focus();
  });
  byId("library_bulk_add").addEventListener("click", () => open("add"));
  byId("library_bulk_remove").addEventListener("click", () => open("remove"));
  target.addEventListener("change", () => { byId("collection_bulk_status").textContent = ""; renderDialog(); });
  for (const close of dialog.querySelectorAll("[data-bulk-close]")) close.addEventListener("click", () => { if (!busy) dialog.close(); });
  dialog.addEventListener("cancel", event => { if (busy) event.preventDefault(); });
  byId("collection_bulk_form").addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || byId("collection_bulk_submit").disabled) return;
    const creating = mode === "add" && target.value === "new";
    const collection = collections.find(item => item.id === (mode === "remove" ? removeCollectionId : target.value));
    const cloudIds = mode === "add" && !creating ? batchIds.filter(id => !collection.cloudIds.includes(id)) : batchIds;
    busy = true;
    byId("collection_bulk_status").textContent = "";
    renderSelection(); renderDialog();
    try {
      const updated = creating
        ? await api("/api/collections", json("POST", {name: byId("collection_bulk_name").value, cloudIds}))
        : await api(`/api/collections/${collection.id}/clouds`, json(mode === "remove" ? "DELETE" : "POST", {cloudIds}));
      selected.clear();
      dialog.close();
      onMessage(`${mode === "remove" ? "Odłączono" : "Dodano"} chmury: ${cloudIds.length}. Kolekcja: „${updated.name}”.`);
      await onChanged();
      selectAll.focus({preventScroll: true});
    } catch (error) { byId("collection_bulk_status").textContent = error.message; }
    finally { busy = false; renderSelection(); renderDialog(); }
  });

  return {
    checkbox(cloud) {
      const input = element("input", "cloud-selection-checkbox");
      input.type = "checkbox";
      input.dataset.libraryCloud = cloud.id;
      input.setAttribute("aria-label", `Zaznacz chmurę ${cloud.name}`);
      input.addEventListener("change", () => {
        if (input.checked && selected.size >= MAX_SELECTION) {
          onMessage(`Jednorazowo można zaznaczyć do ${MAX_SELECTION} chmur.`);
        } else if (input.checked) selected.add(cloud.id);
        else selected.delete(cloud.id);
        renderSelection();
      });
      return input;
    },
    update(data) {
      if (scope !== data.scope) selected.clear();
      scope = data.scope;
      ({clouds, visible, collections, currentCollection} = data);
      const known = new Set(visible.map(cloud => cloud.id));
      for (const id of selected) if (!known.has(id)) selected.delete(id);
      renderSelection(); renderDialog();
    },
  };
}
