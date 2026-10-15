import {api, json, sizeLabel, active, statusLabel, element} from "./common.js";
const byId = id => document.getElementById(id);
let clouds = [];
let jobs = [];
let selected = null;
let timer;
let toastTimer;
let uploading = false;

function toast(message) {
  byId("toast").textContent = message;
  byId("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { byId("toast").hidden = true; }, 4500);
}

function openUpload() {
  byId("upload_status").textContent = "";
  byId("upload_progress").hidden = true;
  byId("upload_dialog").showModal();
}
byId("add_cloud").addEventListener("click", openUpload);
byId("empty_upload").addEventListener("click", openUpload);
for (const button of document.querySelectorAll("dialog .close")) {
  button.addEventListener("click", () => { if (!uploading) button.closest("dialog").close(); });
}
byId("upload_dialog").addEventListener("cancel", event => { if (uploading) event.preventDefault(); });

function badge(status) {
  return element("span", `badge ${active(status) ? "active" : status === "completed" ? "ready" : status}`, statusLabel(status));
}

function render() {
  byId("total_count").textContent = clouds.length;
  byId("ready_count").textContent = clouds.filter(cloud => cloud.status === "ready").length;
  byId("processing_count").textContent = clouds.filter(cloud => active(cloud.status)).length;
  byId("storage_size").textContent = sizeLabel(clouds.reduce((sum, cloud) => sum + cloud.sizeBytes, 0));
  const query = byId("search").value.toLocaleLowerCase("pl");
  const status = byId("status_filter").value;
  const visible = clouds.filter(cloud => cloud.name.toLocaleLowerCase("pl").includes(query)
    && (status === "all" || (status === "active" ? active(cloud.status) : cloud.status === status)));
  byId("list_count").textContent = visible.length;
  byId("cloud_rows").replaceChildren();
  for (const cloud of visible) {
    const row = element("tr");
    const nameCell = element("td");
    const title = element("div", "cloud-title");
    const icon = element("span", "cloud-icon", "⠿");
    icon.setAttribute("aria-hidden", "true");
    const description = element("div");
    description.append(element("strong", "", cloud.name), element("small", "", cloud.format + (cloud.parentCloud ? " · Wynik przetwarzania" : " · Import")));
    title.append(icon, description);
    nameCell.append(title);
    const statusCell = element("td");
    statusCell.append(badge(cloud.status));
    if (cloud.error) statusCell.title = cloud.error;
    const visibility = element("td", "visibility", cloud.visibility === "public" ? "◉ Publiczna" : "◈ Prywatna");
    const date = new Date(cloud.createdAt).toLocaleString("pl-PL", {
      day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
    });
    const actionsCell = element("td");
    const actions = element("div", "row-actions");
    if (cloud.status === "ready") {
      const open = element("a", "", "Otwórz ↗");
      open.href = cloud.viewerUrl;
      open.setAttribute("aria-label", `Otwórz chmurę ${cloud.name}`);
      actions.append(open);
    }
    const edit = element("button", "quiet", "Edytuj");
    edit.type = "button";
    edit.addEventListener("click", () => {
      selected = cloud;
      byId("edit_name").value = cloud.name;
      byId("edit_visibility").value = cloud.visibility;
      byId("edit_status").textContent = "";
      byId("edit_dialog").showModal();
    });
    const remove = element("button", "quiet", "Usuń");
    remove.type = "button";
    remove.disabled = active(cloud.status);
    remove.title = remove.disabled ? "Poczekaj na zakończenie przetwarzania" : `Usuń ${cloud.name}`;
    remove.addEventListener("click", () => {
      selected = cloud;
      byId("delete_name").textContent = cloud.name;
      byId("delete_status").textContent = "";
      byId("delete_dialog").showModal();
    });
    actions.append(edit, remove);
    actionsCell.append(actions);
    row.append(nameCell, statusCell, visibility, element("td", "muted", sizeLabel(cloud.sizeBytes)), element("td", "muted", date), actionsCell);
    byId("cloud_rows").append(row);
  }
  byId("empty_state").hidden = visible.length > 0;
  byId("empty_title").textContent = clouds.length ? "Brak pasujących chmur" : "Twoja biblioteka jest gotowa";
  byId("empty_description").textContent = clouds.length ? "Zmień nazwę wyszukiwania lub wybrany status." : "Dodaj pierwszy plik LAS lub LAZ, aby rozpocząć pracę.";
  byId("empty_upload").hidden = clouds.length > 0;
  byId("load_status").textContent = `${clouds.length} chmur w bibliotece · Statusy aktualizują się automatycznie`;
  renderJobs();
}

function renderJobs() {
  const expanded = new Set([...byId("job_list").querySelectorAll("details[open]")].map(node => node.dataset.id));
  byId("job_list").replaceChildren();
  const names = {import: "Import", segmentation: "Segmentacja", transformation: "Transformacja", "point-removal": "Usuwanie punktów"};
  for (const job of jobs.slice(0, 8)) {
    const details = element("details", "job");
    details.dataset.id = job.id;
    details.open = expanded.has(job.id);
    const summary = element("summary");
    summary.append(element("strong", "", job.name), element("span", "muted", names[job.kind]), badge(job.status));
    if (active(job.status)) {
      const progress = element("progress");
      progress.max = 100;
      if (job.progress > 0) progress.value = job.progress;
      progress.setAttribute("aria-label", `Postęp zadania ${job.name}`);
      summary.append(progress);
    }
    details.append(summary);
    if (job.error) details.append(element("p", "error", job.error));
    details.append(element("pre", "", job.log || "Zadanie oczekuje na rozpoczęcie."));
    byId("job_list").append(details);
  }
  if (!jobs.length) byId("job_list").append(element("p", "muted", "Tutaj zobaczysz przebieg importu i przetwarzania chmur."));
}

async function refresh() {
  clearTimeout(timer);
  try {
    const [cloudData, jobData] = await Promise.all([api("/api/clouds"), api("/api/jobs")]);
    clouds = cloudData.clouds;
    jobs = jobData.jobs;
    render();
  } catch (error) {
    byId("load_status").textContent = error.message;
  } finally {
    timer = setTimeout(refresh, jobs.some(job => active(job.status)) ? 2000 : 10000);
  }
}
byId("search").addEventListener("input", render);
byId("status_filter").addEventListener("change", render);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

byId("upload_file").addEventListener("change", () => {
  const file = byId("upload_file").files[0];
  if (!file) return;
  byId("file_label").textContent = `${file.name} · ${sizeLabel(file.size)}`;
  if (!byId("upload_name").value) byId("upload_name").value = file.name.replace(/\.(las|laz)$/i, "").slice(0, 120);
});
byId("upload_form").addEventListener("submit", event => {
  event.preventDefault();
  if (uploading) return;
  uploading = true;
  byId("upload_submit").disabled = true;
  byId("upload_progress").hidden = false;
  byId("upload_status").textContent = "Przesyłanie pliku…";
  const file = byId("upload_file").files[0];
  const form = new FormData(byId("upload_form"));
  const query = new URLSearchParams({filename: file.name, name: form.get("name"), visibility: form.get("visibility")});
  const request = new XMLHttpRequest();
  // Stream large LAS/LAZ files straight into the application's upload directory.
  request.open("POST", `/api/conversions?${query}`);
  request.setRequestHeader("Content-Type", "application/octet-stream");
  request.upload.addEventListener("progress", event => {
    if (event.lengthComputable) {
      const percent = Math.round(event.loaded / event.total * 100);
      byId("upload_progress").value = percent;
      byId("upload_status").textContent = percent === 100 ? "Sprawdzanie i zapisywanie pliku…" : `Przesyłanie: ${percent}%`;
    }
  });
  request.addEventListener("load", async () => {
    let data;
    try { data = JSON.parse(request.responseText); } catch { data = {error: "Serwer zwrócił nieprawidłową odpowiedź."}; }
    if (request.status === 202) {
      byId("upload_dialog").close();
      byId("upload_form").reset();
      byId("file_label").textContent = "Oryginał zostanie zachowany do dalszego przetwarzania.";
      toast("Plik przesłany. Chmura oczekuje na konwersję.");
      await refresh();
    } else if (request.status === 401) {
      window.location.assign("/login");
    } else byId("upload_status").textContent = data.errors?.[0]?.message || data.error || "Upload nie powiódł się.";
  });
  request.addEventListener("error", () => { byId("upload_status").textContent = "Utracono połączenie z serwerem."; });
  request.addEventListener("loadend", () => { uploading = false; byId("upload_submit").disabled = false; });
  request.send(file);
});

for (const [name, action] of [
  ["edit", async cloud => api(`/api/clouds/${cloud.id}`, json("PATCH", {name: byId("edit_name").value, visibility: byId("edit_visibility").value}))],
  ["delete", async cloud => api(`/api/clouds/${cloud.id}`, {method: "DELETE"})],
]) {
  byId(`${name}_form`).addEventListener("submit", async event => {
    event.preventDefault();
    const submit = event.target.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      await action(selected);
      byId(`${name}_dialog`).close();
      toast(name === "edit" ? "Zmiany zapisane." : "Chmura została usunięta.");
      await refresh();
    } catch (error) { byId(`${name}_status`).textContent = error.message; }
    finally { submit.disabled = false; }
  });
}
byId("logout").addEventListener("click", async () => {
  try { await api("/api/auth/logout", {method: "POST"}); window.location.assign("/login"); }
  catch (error) { toast(error.message); }
});

async function initialize() {
  try {
    const [account, health] = await Promise.all([api("/api/auth/me"), api("/api/health")]);
    byId("account_email").textContent = account.user.email;
    if (!health.converterAvailable) {
      byId("add_cloud").disabled = byId("empty_upload").disabled = true;
      toast("PotreeConverter jest niedostępny. Istniejące chmury można nadal otwierać.");
    }
    await refresh();
  } catch (error) { byId("load_status").textContent = error.message; }
}
initialize();
