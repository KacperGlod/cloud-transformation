import {api, element} from "./common.js";

const pending = new Set();

export function createJobControls({onRetry, onChange, onError}) {
  const container = element("div", "job-controls");
  container.setAttribute("role", "group");
  container.setAttribute("aria-label", "Sterowanie zadaniem");
  let signature;

  function update(job) {
    const next = JSON.stringify([job?.id, job?.status, job?.canCancel, job?.canRetry,
      job?.retryUnavailableReason, pending.has(job?.id)]);
    if (signature === next) return;
    signature = next;
    container.replaceChildren();
    if (!job) return;
    const retry = ["failed", "cancelled"].includes(job.status);
    if (!job.canCancel && !retry && job.status !== "cancelling") return;
    const label = job.status === "queued" ? "Usuń z kolejki"
      : job.status === "cancelling" ? "Zatrzymywanie…"
      : job.status === "failed" ? "Ponów po błędzie"
      : job.status === "cancelled" ? "Uruchom ponownie" : "Zatrzymaj operację";
    const button = element("button", "job-action-button secondary-button secondary", label);
    button.type = "button";
    button.disabled = pending.has(job.id) || !(job.canCancel || job.canRetry);
    button.setAttribute("aria-label", `${label}: ${job.name}`);
    if (job.retryUnavailableReason) button.title = job.retryUnavailableReason;
    button.addEventListener("click", async event => {
      event.preventDefault();
      event.stopPropagation();
      if (pending.has(job.id)) return;
      pending.add(job.id);
      update(job);
      try {
        const nextJob = await api(`/api/jobs/${encodeURIComponent(job.id)}/${retry ? "retry" : "cancel"}`, {method: "POST"});
        pending.delete(job.id);
        // The viewer's poller must observe cancellation before a retry can start another poller.
        if (retry || !onRetry) update(nextJob);
        onChange?.(nextJob);
        if (retry) onRetry?.(nextJob);
      } catch (error) {
        pending.delete(job.id);
        update(job);
        onError(error);
      }
    });
    container.append(button);
    if (retry && job.retryUnavailableReason) container.append(element("p", "job-action-hint", job.retryUnavailableReason));
  }

  return {container, update};
}

export function checkJobCancellation(job) {
  if (job.status === "cancelled") {
    const error = new Error("Zadanie zostało anulowane. Chmura źródłowa została zachowana.");
    error.cancelled = true;
    throw error;
  }
  return job.status === "cancelling";
}
