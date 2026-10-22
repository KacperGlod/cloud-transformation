import {api, element, sizeLabel, statusLabel} from "./common.js";

const number = value => Number.isFinite(value) ? value.toLocaleString("pl-PL", {
  maximumFractionDigits: 6, ...(value !== 0 && Math.abs(value) < .000001 ? {notation: "scientific"} : {}),
}) : "Nieznane";
const vector = value => Array.isArray(value) ? value.map(number).join("; ") : "Nieznane";
const date = value => new Date(value).toLocaleString("pl-PL", {dateStyle: "medium", timeStyle: "short"});
const unit = value => ({metre: "metry (m)", foot: "stopy (ft)", "US survey foot": "stopy US (ftUS)",
  degree: "stopnie (°)", radian: "radiany (rad)", grad: "grady (gon)"}[value] || value || "Nieznane");
const operations = {import: "Import", segmentation: "Segmentacja", transformation: "Transformacja", "point-removal": "Usuwanie punktów"};
const attributes = {position: "Pozycja XYZ", rgb: "Kolory RGB", rgba: "Kolory RGBA", intensity: "Intensywność",
  classification: "Klasyfikacja", "return number": "Numer odbicia", "number of returns": "Liczba odbić",
  "gps-time": "Czas GPS", "scan angle": "Kąt skanowania", "scan angle rank": "Kąt skanowania", "point source id": "Identyfikator źródła",
  "classification flags": "Flagi klasyfikacji", "user data": "Dane użytkownika"};

function section(container, title) {
  const node = element("section", "cloud-info-section");
  node.append(element("h3", "", title));
  const list = element("dl", "cloud-info-fields");
  node.append(list);
  container.append(node);
  return {node, list};
}

function field(list, label, value) {
  const content = element("dd");
  if (value instanceof Node) content.append(value);
  else content.textContent = value ?? "Nieznane";
  list.append(element("dt", "", label), content);
}

function operationFields(list, operation) {
  field(list, "Operacja", operations[operation.type] || operation.type || "Import");
  if (operation.type === "transformation") {
    field(list, "Przesunięcie XYZ", vector(operation.translation));
    field(list, "Obrót XYZ (°)", vector(operation.rotationDegrees));
    field(list, "Skala XYZ", vector(operation.scale));
    field(list, "Punkt obrotu", {center: "Środek chmury", origin: "Początek układu", custom: "Własny punkt"}[operation.pivotMode] || "Nieznany");
    if (operation.result?.pivot || operation.pivotMode === "custom") {
      field(list, "Współrzędne punktu", vector(operation.result?.pivot || operation.pivot));
    }
  } else if (operation.type === "segmentation") {
    field(list, "Zachowane punkty", operation.mode === "inside" ? "Wewnątrz obszaru" : operation.mode === "outside" ? "Poza obszarem" : "Nieznane");
    if (operation.clipBox) {
      field(list, "Pozycja obszaru XYZ", vector(operation.clipBox.position));
      field(list, "Rozmiar obszaru XYZ", vector(operation.clipBox.scale));
      field(list, "Obrót obszaru (rad)", vector(operation.clipBox.rotation));
    }
  } else if (operation.type === "point-removal") {
    field(list, "Liczba zaznaczeń", number(operation.selections?.length));
  }
  const result = operation.result || {};
  for (const [key, label] of [["inputPoints", "Punkty wejściowe"], ["outputPoints", "Punkty wynikowe"],
    ["selectedPoints", "Wybrane punkty"], ["rejectedPoints", "Odrzucone punkty"],
    ["removedPoints", "Usunięte punkty"], ["skippedPoints", "Pominięte punkty"], ["scanCount", "Liczba skanów"]]) {
    if (Number.isFinite(result[key])) field(list, label, number(result[key]));
  }
}

function renderInfo(container, info) {
  const {cloud} = info;
  const basics = section(container, "Podstawowe dane");
  field(basics.list, "Nazwa", cloud.name);
  field(basics.list, "Status", statusLabel(cloud.status));
  field(basics.list, "Dostęp", cloud.visibility === "public" ? "Publiczna" : "Prywatna");
  field(basics.list, "Format pliku", info.sourceFormat);
  field(basics.list, "Format widoku", cloud.format);
  field(basics.list, "Rozmiar pliku", sizeLabel(cloud.sizeBytes));
  field(basics.list, "Dodano", date(cloud.createdAt));
  field(basics.list, "Zaktualizowano", date(cloud.updatedAt));
  if (cloud.sourceFile) field(basics.list, "Plik źródłowy", cloud.sourceFile);

  const geometry = section(container, "Geometria");
  field(geometry.list, "Liczba punktów", number(info.pointCount));
  field(geometry.list, "Jednostki XY", unit(info.reference.horizontalUnit));
  field(geometry.list, "Jednostki Z", unit(info.reference.verticalUnit));
  field(geometry.list, "Zadeklarowany CRS", [info.reference.authority, info.reference.name].filter(Boolean).join(" · ") || "Nieznany");
  if (!info.reference.name) geometry.node.append(element("p", "cloud-info-note", "Brak rozpoznanego układu współrzędnych w metadanych."));
  if (info.bounds) {
    const table = element("table", "cloud-info-bounds");
    const caption = element("caption", "", "Zakres współrzędnych XYZ");
    const head = element("thead");
    const titles = element("tr");
    for (const title of ["Oś", "Minimum", "Maksimum"]) {
      const cell = element("th", "", title);
      cell.scope = "col";
      titles.append(cell);
    }
    head.append(titles);
    const body = element("tbody");
    for (let index = 0; index < 3; index++) {
      const row = element("tr");
      const axis = element("th", "", "XYZ"[index]);
      axis.scope = "row";
      row.append(axis, element("td", "", number(info.bounds.min[index])), element("td", "", number(info.bounds.max[index])));
      body.append(row);
    }
    table.append(caption, head, body);
    geometry.node.append(table);
  } else geometry.node.append(element("p", "cloud-info-note", "Zakres XYZ jest niedostępny."));
  const precision = element("details", "cloud-info-precision");
  precision.append(element("summary", "", "Szczegóły zapisu współrzędnych"));
  const precisionFields = element("dl", "cloud-info-fields");
  field(precisionFields, "Skala zapisu XYZ", vector(info.scale));
  field(precisionFields, "Offset XYZ", vector(info.offset));
  if (info.lasVersion) field(precisionFields, "Wersja LAS", info.lasVersion);
  if (info.pointFormat !== null) field(precisionFields, "Format rekordu LAS", String(info.pointFormat));
  precision.append(precisionFields);
  geometry.node.append(precision);

  const attributeSection = section(container, "Atrybuty we viewerze");
  attributeSection.list.remove();
  if (info.attributes?.length) {
    const chips = element("ul", "cloud-info-attributes");
    for (const name of info.attributes) chips.append(element("li", "", attributes[name] || name));
    attributeSection.node.append(chips);
  } else attributeSection.node.append(element("p", "cloud-info-note", "Lista atrybutów jest niedostępna."));

  const origin = section(container, "Pochodzenie");
  if (cloud.canEdit) {
    if (info.source) {
      const source = element(info.source.viewerUrl ? "a" : "span", "cloud-info-source", info.source.name);
      if (info.source.viewerUrl) source.href = info.source.viewerUrl;
      field(origin.list, "Chmura źródłowa", source);
      field(origin.list, "Status źródła", info.source.status === "deleted" ? "Usunięta" : statusLabel(info.source.status));
    } else if (cloud.parentCloud) field(origin.list, "Chmura źródłowa", "Niedostępna");
    else field(origin.list, "Pochodzenie", "Import pliku");
    if (cloud.operation) operationFields(origin.list, cloud.operation);
    else if (cloud.parentCloud) field(origin.list, "Parametry operacji", "Brak zapisanych parametrów");
  } else {
    origin.list.remove();
    origin.node.append(element("p", "cloud-info-note", "Szczegóły pochodzenia i parametry operacji są dostępne właścicielowi chmury."));
  }
  for (const notice of info.notices) container.append(element("p", "cloud-info-notice", notice));
  if (info.metadataSource) container.append(element("p", "cloud-info-note cloud-info-footer", `Źródło danych geometrycznych: ${info.metadataSource}.`));
}

function renderCollections(container, info, onSelect) {
  if (!info.cloud.canEdit) {
    container.append(element("p", "cloud-info-note", "Przynależność do kolekcji jest widoczna tylko właścicielowi chmury."));
  } else if (!Array.isArray(info.collections)) {
    container.append(element("p", "cloud-info-note", "Informacje o kolekcjach są niedostępne."));
  } else if (!info.collections.length) {
    container.append(element("p", "cloud-info-note", "Ta chmura nie należy do żadnej kolekcji."));
  } else {
    const group = section(container, "Kolekcje tej chmury");
    group.list.remove();
    const list = element("ul", "cloud-info-collections");
    for (const collection of info.collections) {
      const item = element("li");
      const link = element("a", "cloud-info-collection-link", collection.name);
      link.href = `/?collection=${encodeURIComponent(collection.id)}`;
      link.title = `Pokaż chmury z kolekcji ${collection.name}`;
      if (onSelect) link.addEventListener("click", event => {
        if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        if (onSelect(collection)) event.preventDefault();
      });
      item.append(link);
      list.append(item);
    }
    group.node.append(list);
  }
}

export function createCloudInfoPanel({onCollectionSelect} = {}) {
  const dialog = element("dialog", "cloud-info-panel");
  dialog.setAttribute("aria-labelledby", "cloud_info_heading");
  const header = element("header", "cloud-info-header");
  const heading = element("div");
  const title = element("h2", "", "Informacje o chmurze");
  title.id = "cloud_info_heading";
  const name = element("p", "cloud-info-name");
  heading.append(title, name);
  const refresh = element("button", "cloud-info-icon", "↻");
  refresh.type = "button";
  refresh.title = "Odśwież informacje";
  refresh.setAttribute("aria-label", refresh.title);
  const close = element("button", "cloud-info-icon", "×");
  close.type = "button";
  close.autofocus = true;
  close.setAttribute("aria-label", "Zamknij informacje o chmurze");
  const actions = element("div", "cloud-info-header-actions");
  actions.append(refresh, close);
  header.append(heading, actions);
  const body = element("div", "cloud-info-body");
  const status = element("p", "cloud-info-status");
  status.setAttribute("role", "status");
  const content = element("div");
  const collectionContent = element("div");
  const tabList = element("div", "cloud-info-tabs");
  tabList.setAttribute("role", "tablist");
  tabList.setAttribute("aria-label", "Szczegóły chmury");
  const tabs = [["details", "Informacje", content], ["collections", "Kolekcje", collectionContent]].map(([key, label, panel]) => {
    const button = element("button", "cloud-info-tab", label);
    button.type = "button";
    button.id = `cloud_info_${key}_tab`;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", `cloud_info_${key}`);
    panel.id = `cloud_info_${key}`;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", button.id);
    panel.tabIndex = 0;
    tabList.append(button);
    return {button, panel};
  });
  function selectTab(index, focus = false) {
    tabs.forEach(({button, panel}, position) => {
      const selected = position === index;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      panel.hidden = !selected;
    });
    body.scrollTop = 0;
    if (focus) tabs[index].button.focus();
  }
  tabs.forEach(({button}, index) => {
    button.addEventListener("click", () => selectTab(index));
    button.addEventListener("keydown", event => {
      const destination = {ArrowRight: (index + 1) % tabs.length,
        ArrowLeft: (index + tabs.length - 1) % tabs.length, Home: 0, End: tabs.length - 1}[event.key];
      if (destination === undefined) return;
      event.preventDefault();
      selectTab(destination, true);
    });
  });
  selectTab(0);
  body.append(status, content, collectionContent);
  dialog.append(header, tabList, body);
  document.body.append(dialog);
  let identifier, trigger, controller, requestVersion = 0;

  async function load() {
    controller?.abort();
    controller = new AbortController();
    const version = ++requestVersion;
    content.replaceChildren();
    collectionContent.replaceChildren();
    tabs[1].button.textContent = "Kolekcje";
    status.textContent = "Wczytywanie informacji…";
    body.setAttribute("aria-busy", "true");
    try {
      const info = await api(`/api/clouds/${encodeURIComponent(identifier)}/info`, {signal: controller.signal});
      if (version !== requestVersion || !dialog.open) return;
      name.textContent = info.cloud.name;
      renderInfo(content, info);
      renderCollections(collectionContent, info, onCollectionSelect ? collection => {
        // Local navigation returns its focus target; otherwise the regular library link is used.
        const focusTarget = onCollectionSelect(collection);
        if (!focusTarget) return false;
        trigger = focusTarget;
        dialog.close();
        return true;
      } : null);
      if (info.cloud.canEdit && Array.isArray(info.collections)) tabs[1].button.textContent = `Kolekcje (${info.collections.length})`;
      status.textContent = "";
    } catch (error) {
      if (version !== requestVersion || error.name === "AbortError" || !dialog.open) return;
      status.textContent = `Nie udało się wczytać informacji. ${error.message} Użyj przycisku „Odśwież informacje”, aby spróbować ponownie.`;
    } finally {
      if (version === requestVersion) body.setAttribute("aria-busy", "false");
    }
  }

  close.addEventListener("click", () => dialog.close());
  refresh.addEventListener("click", load);
  dialog.addEventListener("click", event => {
    if (event.target !== dialog) return;
    const box = dialog.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close();
  });
  // Keep viewer shortcuts from handling typing, Enter, or Escape inside the modal.
  for (const type of ["keydown", "keyup"]) dialog.addEventListener(type, event => event.stopPropagation());
  dialog.addEventListener("close", () => {
    controller?.abort();
    requestVersion++;
    // Polling replaces library rows; restore focus to the replacement name button.
    const target = trigger?.isConnected ? trigger : document.querySelector(`[data-cloud-info-id="${CSS.escape(identifier)}"]`);
    target?.focus({preventScroll: true});
  });

  return {
    open(id, label, source = document.activeElement) {
      identifier = id;
      trigger = source;
      name.textContent = label;
      body.scrollTop = 0;
      selectTab(0);
      if (!dialog.open) dialog.showModal();
      load();
    },
  };
}
