type MapWithGetOrInsertComputed = Map<unknown, unknown> & {
  getOrInsertComputed?: (key: unknown, callback: (key: unknown) => unknown) => unknown;
};

const mapPrototype = Map.prototype as MapWithGetOrInsertComputed;

// pdfjs-dist 6 uses the Map upsert proposal, which is not available in Electron 39.
if (typeof mapPrototype.getOrInsertComputed !== "function") {
  Object.defineProperty(mapPrototype, "getOrInsertComputed", {
    configurable: true,
    writable: true,
    value(this: Map<unknown, unknown>, key: unknown, callback: (key: unknown) => unknown) {
      if (this.has(key)) return this.get(key);
      const value = callback(key);
      this.set(key, value);
      return value;
    },
  });
}
