import {api, element, statusLabel} from "./common.js";

const operationNames = {import: "Import", segmentation: "Segmentacja", transformation: "Transformacja",
  "point-removal": "Usuwanie punktów", processing: "Przetwarzanie"};
export const operationLabel = type => Object.hasOwn(operationNames, type) ? operationNames[type] : "Przetwarzanie";

// Traverse iteratively so legacy cycles and very deep processing chains cannot recurse forever.
export function orderedLineage(nodes) {
  const known = new Map(nodes.map(node => [node.id, node]));
  const children = new Map(nodes.map(node => [node.id, []]));
  const roots = [];
  for (const node of nodes) {
    if (node.parentId !== node.id && known.has(node.parentId)) children.get(node.parentId).push(node);
    else roots.push(node);
  }
  const visited = new Set(), ordered = [];
  for (const root of [...roots, ...nodes]) {
    const stack = [{node: root, depth: 0}];
    while (stack.length) {
      const item = stack.pop();
      if (visited.has(item.node.id)) continue;
      visited.add(item.node.id);
      ordered.push(item);
      for (const child of [...children.get(item.node.id)].reverse()) stack.push({node: child, depth: item.depth + 1});
    }
  }
  return ordered;
}

function renderLineage(container, history, onInspect) {
  const heading = element("h3", "", "Historia pochodzenia");
  heading.className = "cloud-lineage-heading";
  container.append(heading);
  if (history.nodes.length === 1 && !history.nodes[0].parentId && !history.nodes[0].sourceUnavailable) {
    container.append(element("p", "cloud-info-note", "Ta chmura nie ma zapisanych powiązań z innymi chmurami."));
  }
  for (const notice of history.notices) container.append(element("p", "cloud-info-notice", notice));
  const known = new Map(history.nodes.map(node => [node.id, node]));
  const list = element("ol", "cloud-lineage-list");
  list.setAttribute("aria-label", "Chmury w rodzinie przetwarzania");
  for (const {node, depth} of orderedLineage(history.nodes)) {
    const selected = node.id === history.cloudId;
    const item = element("li", depth ? "cloud-lineage-child" : "");
    item.style.marginInlineStart = `${Math.min(depth, 4) * 14}px`;
    const card = element("article", `cloud-lineage-node${selected ? " is-current" : ""}`);
    card.dataset.lineageId = node.id;
    if (selected) card.setAttribute("aria-current", "true");
    const title = element(node.canInspect && !selected ? "button" : "strong", "cloud-lineage-name", node.name);
    if (title instanceof HTMLButtonElement) {
      title.type = "button";
      title.title = "Pokaż szczegóły tej chmury";
      title.setAttribute("aria-label", `Informacje o chmurze ${node.name}`);
      title.addEventListener("click", () => onInspect(node));
    }
    const header = element("div", "cloud-lineage-node-header");
    header.append(title);
    if (selected) header.append(element("span", "cloud-lineage-current", "Wybrana chmura"));
    const time = new Date(node.createdAt).toLocaleString("pl-PL", {dateStyle: "medium", timeStyle: "short"});
    card.append(header, element("p", "cloud-lineage-meta", `${operationLabel(node.operationType)} · ${time}`));
    const source = known.get(node.parentId);
    if (source) card.append(element("p", "cloud-lineage-source", `Źródło: ${source.name}`));
    else if (node.sourceUnavailable) card.append(element("p", "cloud-lineage-source", "Źródło jest niedostępne."));
    const actions = element("div", "cloud-lineage-actions");
    actions.append(element("span", "cloud-lineage-status", node.status === "deleted" ? "Usunięta" : statusLabel(node.status)));
    for (const [url, text] of [[node.viewerUrl, "Otwórz ↗"], [node.comparisonUrl, "Porównaj ze źródłem"]]) {
      if (!url) continue;
      const link = element("a", "cloud-lineage-action", text);
      link.href = url;
      link.setAttribute("aria-label", `${text.replace(" ↗", "")}: ${node.name}`);
      actions.append(link);
    }
    card.append(actions); item.append(card); list.append(item);
  }
  container.append(list);
  container.querySelector('[aria-current="true"]')?.scrollIntoView({block: "nearest"});
}

export function createCloudLineage({onInspect}) {
  const container = element("div");
  let identifier, owned = false, loaded = false, loading = false, controller, version = 0;

  function reset() {
    controller?.abort(); version++;
    identifier = null; loaded = loading = owned = false;
    container.replaceChildren();
    container.setAttribute("aria-busy", "false");
  }

  async function load() {
    if (!identifier || !owned || loaded || loading) return;
    const requestVersion = ++version;
    controller = new AbortController();
    loading = true;
    container.setAttribute("aria-busy", "true");
    const status = element("p", "cloud-info-note", "Wczytywanie historii pochodzenia…");
    status.setAttribute("role", "status");
    container.replaceChildren(status);
    try {
      const history = await api(`/api/clouds/${encodeURIComponent(identifier)}/lineage`, {signal: controller.signal});
      if (requestVersion !== version) return;
      container.replaceChildren();
      renderLineage(container, history, onInspect);
      loaded = true;
    } catch (error) {
      if (requestVersion !== version || error.name === "AbortError") return;
      status.textContent = `Nie udało się wczytać historii. ${error.message}`;
      const retry = element("button", "cloud-lineage-retry", "Spróbuj ponownie");
      retry.type = "button";
      retry.addEventListener("click", load);
      container.append(retry);
    } finally {
      if (requestVersion === version) { loading = false; container.setAttribute("aria-busy", "false"); }
    }
  }

  return {
    container, reset, load,
    setCloud(cloud) {
      reset(); identifier = cloud.id; owned = cloud.canEdit;
      if (!owned) container.append(element("p", "cloud-info-note", "Historia pochodzenia jest dostępna tylko właścicielowi chmury."));
    },
  };
}
