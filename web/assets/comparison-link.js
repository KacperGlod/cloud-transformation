export function createComparisonLink(panel, currentCloud) {
  const link = document.createElement("a");
  link.textContent = "Porównaj z oryginałem ↗";
  link.className = "secondary-button";
  link.style.cssText = "display:block;text-align:center;margin-top:.75rem;text-decoration:none";
  const container = document.createElement("div");
  container.append(link);
  container.hidden = true;
  panel.querySelector(".actions").after(container);
  let revision = 0;

  return {
    async update() {
      const currentRevision = ++revision;
      const cloud = currentCloud();
      container.hidden = true;
      link.removeAttribute("href");
      if (!cloud?.id || !cloud.parentCloud) return;
      try {
        const response = await fetch(`/api/clouds/${encodeURIComponent(cloud.id)}/comparison`, {cache: "no-store"});
        if (currentRevision !== revision || !response.ok) return;
        link.href = `/compare?cloud=${encodeURIComponent(cloud.id)}`;
        container.hidden = false;
      } catch {
        // The regular viewer remains usable if comparison metadata cannot be fetched.
      }
    },
  };
}
