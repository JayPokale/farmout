'use strict';

// Deep-merge user overrides (parsed from a JSON request body) into a copy of the defaults.
function merge(target, source) {
  for (const key in source) {
    const value = source[key];
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      target[key] = merge(target[key] || {}, value);
    } else {
      target[key] = value;
    }
  }
  return target;
}

// The smallest upload size tier (in bytes) that fits `bytes`.
function pickTier(tiers, bytes) {
  const sorted = tiers.slice().sort();
  return sorted.find((t) => t >= bytes);
}

function loadConfig(defaults, overridesJson) {
  return merge(structuredClone(defaults), JSON.parse(overridesJson));
}

module.exports = { merge, pickTier, loadConfig };
