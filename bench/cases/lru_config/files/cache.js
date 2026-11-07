'use strict';
const fs = require('fs');

// Least-recently-used cache whose entries also expire after `ttlSeconds`.
class LRUCache {
  constructor(maxEntries = 500, ttlSeconds = 60) {
    this.max = maxEntries;
    this.ttl = ttlSeconds;
    this.map = new Map();
  }

  get(key) {
    const entry = this.map.get(key);
    if (!entry || this.expired(entry)) {
      this.map.delete(key);
      return undefined;
    }
    return entry.value;
  }

  set(key, value) {
    this.map.delete(key);
    this.map.set(key, { value, at: Date.now() });
    if (this.map.size > this.max) {
      this.map.delete(this.map.keys().next().value);
    }
  }

  expired(entry) {
    return Date.now() - entry.at > this.ttl;
  }

  delete(key) {
    return this.map.delete(key);
  }

  get size() {
    return this.map.size;
  }

  async save(path) {
    const data = JSON.stringify([...this.map.entries()]);
    fs.promises.writeFile(path, data);
  }
}

module.exports = { LRUCache };
