export async function api(url, options = {}) {
  const response = await fetch(url, {cache: "no-store", ...options});
  if (response.status === 401) {
    window.location.assign("/login");
    throw new Error("Sesja wygasła. Zaloguj się ponownie.");
  }
  if (response.status === 204) return null;
  const data = await response.json();
  if (!response.ok) throw new Error(data.errors?.[0]?.message || data.error || "Operacja nie powiodła się.");
  return data;
}

export const json = (method, data) => ({method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});

export function sizeLabel(value) {
  if (!value) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toLocaleString("pl-PL", {maximumFractionDigits: 1})} ${units[index]}`;
}

export const active = status => ["uploading", "queued", "processing", "normalizing", "converting", "segmenting", "transforming", "removing"].includes(status);
export const statusLabel = status => ({uploading: "Przesyłanie", queued: "W kolejce", processing: "Przetwarzanie", normalizing: "Przygotowanie", converting: "Konwersja", segmenting: "Segmentacja", transforming: "Transformacja", removing: "Usuwanie punktów", ready: "Gotowa", completed: "Ukończono", failed: "Błąd"}[status] || status);
export function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
