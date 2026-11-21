import assert from "node:assert/strict";
import test from "node:test";
import {libraryViewPath, rememberLibraryView, previousLibraryView, libraryReturnUrl, returningViewerCloud} from "../web/assets/library-navigation.js";

const origin = "http://localhost:1234";
const key = "cloud-transformation:library-return";

function browser(t, {referrer = "", saved, blocked = false} = {}) {
  const oldWindow = globalThis.window;
  const oldDocument = globalThis.document;
  const storage = new Map(saved === undefined ? [] : [[key, saved]]);
  globalThis.window = {
    location: {origin, href: `${origin}/?collection=first&q=pomiar&status=ready`},
    sessionStorage: {
      setItem(name, value) { if (blocked) throw new Error("Storage blocked"); storage.set(name, value); },
      getItem(name) { if (blocked) throw new Error("Storage blocked"); return storage.get(name) ?? null; },
    },
  };
  globalThis.document = {referrer};
  t.after(() => {
    globalThis.window = oldWindow;
    globalThis.document = oldDocument;
  });
  return storage;
}

test("library paths retain collection, exact search and status and omit unrelated URL data", () => {
  const url = new URL("/clouds", origin);
  url.searchParams.set("q", "Łąka & skan + 2");
  url.searchParams.set("status", "ready");
  url.searchParams.set("collection", "unassigned");
  url.searchParams.set("other", "value");
  url.hash = "section";
  const path = libraryViewPath(url.href, origin);
  const restored = new URL(path, origin);
  assert.equal(restored.pathname, "/clouds");
  assert.deepEqual([...restored.searchParams], [
    ["collection", "unassigned"], ["q", "Łąka & skan + 2"], ["status", "ready"],
  ]);
  assert.equal(restored.hash, "");
  assert.equal(libraryViewPath("/?collection=all&q=&status=all", origin), "/");
});

test("return paths reject other origins and application pages", () => {
  for (const address of ["", "https://example.com/", "//example.com/clouds", "javascript:alert(1)",
    "/viewer?cloud=first", "/login", "/api/clouds", "/clouds/../viewer", "http://["]) {
    assert.equal(libraryViewPath(address, origin), null, address);
  }
});

test("return links highlight the current cloud without changing the saved filters", () => {
  const path = "/?collection=first&q=pomiar&status=ready";
  const link = libraryReturnUrl(path, "current-cloud", origin);
  assert.equal(link, `${path}&highlight=current-cloud`);
  assert.equal(libraryViewPath(link, origin), path);
  assert.equal(libraryReturnUrl(link, "another-cloud", origin), `${path}&highlight=another-cloud`);
  assert.equal(libraryReturnUrl(path, null, origin), path);
});

test("highlight return links keep cloud IDs encoded and reject external return locations", () => {
  const link = libraryReturnUrl("https://example.com/", "cloud & name#1", origin);
  const url = new URL(link, origin);
  assert.equal(url.origin, origin);
  assert.equal(url.pathname, "/");
  assert.equal(url.searchParams.get("highlight"), "cloud & name#1");
  assert.equal(url.hash, "");
});

test("a return without an explicit highlight identifies the cloud from a local viewer", () => {
  assert.equal(returningViewerCloud(`${origin}/viewer?cloud=first`, "navigate", origin), "first");
  assert.equal(returningViewerCloud(`${origin}/viewer?collection=selected&cloud=last-active`, "navigate", origin), "last-active");
});

test("viewer referrers do not repeat highlights on reload or infer clouds from other pages", () => {
  for (const type of ["reload", "back_forward", undefined]) {
    assert.equal(returningViewerCloud(`${origin}/viewer?cloud=first`, type, origin), null);
  }
  for (const address of ["", "http://[", "https://example.com/viewer?cloud=first", `${origin}/?cloud=first`,
    `${origin}/viewer?collection=first`, `${origin}/viewer?cloud=`]) {
    assert.equal(returningViewerCloud(address, "navigate", origin), null, address);
  }
});

test("the referring library wins over stale saved state and persists for further navigation", t => {
  const storage = browser(t, {
    referrer: `${origin}/?collection=current&q=budynek&status=ready`,
    saved: "/?collection=old&status=failed",
  });
  assert.equal(previousLibraryView(), "/?collection=current&q=budynek&status=ready");
  assert.equal(storage.get(key), previousLibraryView());
  document.referrer = `${origin}/viewer?cloud=other`;
  assert.equal(previousLibraryView(), "/?collection=current&q=budynek&status=ready");
});

test("a direct viewer visit uses this tab's saved library and invalid saved values fall back", t => {
  const storage = browser(t, {saved: "/?q=skan&status=active"});
  assert.equal(previousLibraryView(), "/?q=skan&status=active");
  storage.set(key, "https://example.com/");
  assert.equal(previousLibraryView(), "/");
});

test("remembering the library updates filters and ignores viewer URLs", t => {
  const storage = browser(t);
  rememberLibraryView();
  assert.equal(storage.get(key), "/?collection=first&q=pomiar&status=ready");
  window.location.href = `${origin}/viewer?cloud=second`;
  rememberLibraryView();
  assert.equal(storage.get(key), "/?collection=first&q=pomiar&status=ready");
});

test("blocked storage keeps referrer navigation working and allows a safe default", t => {
  browser(t, {referrer: `${origin}/?q=test&status=ready`, blocked: true});
  assert.doesNotThrow(() => rememberLibraryView());
  assert.equal(previousLibraryView(), "/?q=test&status=ready");
  document.referrer = "";
  assert.equal(previousLibraryView(), "/");
});
