// Metadata loads are shared by single-cloud and collection views. A late result
// after a timeout must never attach itself to a newer scene.
export function createPointcloudCache({load, prepare, timeoutMs = 45_000}) {
  const loaded = new Map(), pending = new Map();
  return {
    values: () => loaded.values(),
    get(url, name) {
      if (loaded.has(url)) return Promise.resolve(loaded.get(url));
      if (pending.has(url)) return pending.get(url);
      let settled = false;
      const request = new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
          settled = true;
          reject(new Error(`Przekroczono czas wczytywania chmury „${name}”. Spróbuj ponownie.`));
        }, timeoutMs);
        Promise.resolve().then(() => load(url, name)).then(pointcloud => {
          if (settled) return;
          clearTimeout(timeout);
          prepare(pointcloud);
          loaded.set(url, pointcloud);
          settled = true;
          resolve(pointcloud);
        }).catch(error => {
          clearTimeout(timeout);
          if (!settled) { settled = true; reject(error); }
        });
      }).finally(() => { if (pending.get(url) === request) pending.delete(url); });
      pending.set(url, request);
      return request;
    },
  };
}
