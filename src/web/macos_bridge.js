/* Cocoa bridge for pywebview 6.2.1. No eval / Function constructor or relaxed CSP. */
window.pywebview._createApi = function (functions) {
  for (const entry of functions) {
    const name = entry.func;
    const parts = name.split('.');
    const leaf = parts.pop();
    let target = window.pywebview.api;
    for (const part of parts) {
      if (['__proto__', 'prototype', 'constructor'].includes(part)) throw Error('Invalid bridge name');
      target = target[part] || (target[part] = {});
    }
    if (['__proto__', 'prototype', 'constructor'].includes(leaf)) throw Error('Invalid bridge name');
    window.pywebview._returnValuesCallbacks[name] = {};
    target[leaf] = function (...args) {
      const id = crypto.randomUUID();
      const promise = new Promise((resolve, reject) => {
        window.pywebview._checkValue(name, resolve, reject, id);
      });
      window.pywebview._jsApiCallback(name, args, id);
      return promise;
    };
  }
};
