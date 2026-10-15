// Runs an async action once at a time. A second call while one is in flight is ignored, so a
// double click / double submit cannot create duplicate jobs, uploads or approvals.
export function singleFlight() {
  let busy = false;
  return async (button, fn) => {
    if (busy) return false;
    busy = true;
    if (button) button.disabled = true;
    try { await fn(); } finally {
      busy = false;
      if (button && button.isConnected) button.disabled = false;
    }
    return true;
  };
}
