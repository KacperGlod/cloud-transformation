import {api, element, json} from "./common.js";

export function createProjectSharing({onChanged = async () => {}} = {}) {
  const dialog = element("dialog", "project-sharing-dialog");
  dialog.setAttribute("aria-labelledby", "project_sharing_title");
  const form = element("form");
  const header = element("div", "project-sharing-header");
  const title = element("h2", "", "Udostępnij projekt"); title.id = "project_sharing_title";
  const close = element("button", "project-sharing-close", "×");
  close.type = "button"; close.setAttribute("aria-label", "Zamknij udostępnianie");
  header.append(title, close);
  const name = element("p", "project-sharing-name");
  const label = element("label", "", "Dostęp do projektu");
  const visibility = element("select"); visibility.id = "project_sharing_visibility";
  for (const [value, text] of [["private", "Prywatny — tylko Twoje konto"], ["public", "Publiczny — każdy, kto ma link"]]) {
    const option = element("option", "", text); option.value = value; visibility.append(option);
  }
  label.append(visibility);
  const note = element("p", "project-sharing-note", "Publiczny projekt udostępnia zapisany widok i chmurę do oglądania. Tylko właściciel może zapisać zmiany. Plik źródłowy pozostaje prywatny.");
  const linkGroup = element("div", "project-sharing-link");
  const linkLabel = element("label", "", "Link do projektu");
  const link = element("input"); link.type = "url"; link.readOnly = true; link.id = "project_sharing_link";
  linkLabel.append(link);
  const copy = element("button", "project-sharing-button", "Kopiuj link"); copy.type = "button";
  linkGroup.append(linkLabel, copy);
  const status = element("p", "project-sharing-status"); status.setAttribute("role", "status");
  const actions = element("div", "project-sharing-actions");
  const save = element("button", "project-sharing-save", "Zapisz dostęp"); save.type = "submit";
  actions.append(save);
  form.append(header, name, label, note, linkGroup, status, actions);
  dialog.append(form); document.body.append(dialog);
  let current, busy = false;

  function update() {
    name.textContent = current.name;
    title.textContent = current.canEdit ? "Udostępnij projekt" : "Link do projektu";
    label.hidden = actions.hidden = !current.canEdit;
    linkGroup.hidden = current.visibility !== "public" || !current.cloudAvailable;
    link.value = new URL(current.viewerUrl, window.location.origin).href;
    for (const control of [visibility, save, copy, close]) control.disabled = busy;
    save.disabled = busy || visibility.value === current.visibility;
    save.textContent = busy ? "Zapisywanie…" : "Zapisz dostęp";
  }
  visibility.addEventListener("change", () => { status.textContent = ""; update(); });
  close.addEventListener("click", () => { if (!busy) dialog.close(); });
  dialog.addEventListener("cancel", event => { if (busy) event.preventDefault(); });
  for (const type of ["keydown", "keyup"]) dialog.addEventListener(type, event => event.stopPropagation());
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(link.value);
      status.textContent = "Link skopiowany.";
    } catch {
      link.focus(); link.select();
      status.textContent = "Zaznaczono link. Skopiuj go skrótem Ctrl+C.";
    }
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || !current.canEdit || save.disabled) return;
    const nextVisibility = visibility.value;
    busy = true; status.textContent = ""; update();
    try {
      const updated = await api(`/api/projects/${encodeURIComponent(current.id)}`, json("PATCH", {
        visibility: nextVisibility, revision: current.revision,
      }));
      current = updated; visibility.value = current.visibility;
      status.textContent = current.visibility === "public" ? "Projekt jest publiczny. Możesz skopiować link." : "Projekt jest prywatny. Udostępniony link nie daje już dostępu do projektu.";
      await onChanged(updated);
    } catch (error) { status.textContent = error.message; }
    finally { busy = false; update(); }
  });
  return {
    open(project) {
      if (busy) return;
      current = project; visibility.value = project.visibility; status.textContent = "";
      update(); dialog.showModal();
    },
  };
}
