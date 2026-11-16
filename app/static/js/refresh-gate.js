// One refresh in flight. Explicit user changes invalidate older responses;
// timer ticks only request a follow-up, so slow networks cannot starve renders.
(function (root) {
  function createRefreshGate(work, onError = console.warn) {
    let pending = null, again = false, revision = 0;
    return function refresh(invalidate = true) {
      if (invalidate) revision++;
      if (pending) { again = true; return pending; }
      pending = (async () => {
        do {
          again = false;
          const mine = revision;
          try { await work(() => mine === revision); }
          catch (error) { onError(error); }
        } while (again);
      })().finally(() => { pending = null; });
      return pending;
    };
  }
  root.createRefreshGate = createRefreshGate;
  if (typeof module !== "undefined") module.exports = createRefreshGate;
})(typeof window !== "undefined" ? window : globalThis);
