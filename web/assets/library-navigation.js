const returnKey = "cloud-transformation:library-return";

export function libraryViewPath(address, origin) {
  if (!address) return null;
  let url;
  try {
    url = new URL(address, origin);
  } catch {
    return null;
  }
  if (url.origin !== origin || !["/", "/clouds"].includes(url.pathname)) return null;

  const params = new URLSearchParams();
  const collection = url.searchParams.get("collection");
  const query = url.searchParams.get("q");
  const status = url.searchParams.get("status");
  if (collection && collection !== "all") params.set("collection", collection);
  if (query) params.set("q", query);
  if (status && status !== "all") params.set("status", status);
  const search = params.toString();
  return url.pathname + (search ? `?${search}` : "");
}

export function rememberLibraryView(address = window.location.href) {
  const path = libraryViewPath(address, window.location.origin);
  if (!path) return;
  try {
    window.sessionStorage.setItem(returnKey, path);
  } catch {
    // Returning through a library referrer still works when storage is blocked.
  }
}

export function previousLibraryView() {
  const origin = window.location.origin;
  const referred = libraryViewPath(document.referrer, origin);
  if (referred) {
    rememberLibraryView(referred);
    return referred;
  }
  try {
    return libraryViewPath(window.sessionStorage.getItem(returnKey), origin) || "/";
  } catch {
    return "/";
  }
}

export function libraryReturnUrl(address, cloudId, origin = window.location.origin) {
  const url = new URL(libraryViewPath(address, origin) || "/", origin);
  if (cloudId) url.searchParams.set("highlight", cloudId);
  return url.pathname + url.search;
}

export function returningViewerCloud(referrer, navigationType, origin) {
  if (!referrer || navigationType !== "navigate") return null;
  try {
    const url = new URL(referrer, origin);
    if (url.origin === origin && url.pathname === "/viewer") {
      return url.searchParams.get("cloud") || null;
    }
  } catch {
    // An invalid referrer cannot identify a returning cloud.
  }
  return null;
}
