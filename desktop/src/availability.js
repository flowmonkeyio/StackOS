"use strict";

// Native process/power lifecycle only. Window visibility and notifications have
// no effect. Reconnect repairs this transport; it never replays an occurrence.
function createAvailabilityController({ app, powerMonitor, service, schedule = setTimeout, cancel = clearTimeout }) {
  let enabled = false;
  let awake = true;
  let stopped = false;
  let generation = 0;
  let connection = null;
  let retry = null;

  function close() {
    generation += 1;
    if (retry !== null) cancel(retry);
    retry = null;
    if (connection) connection.close();
    connection = null;
  }

  function connect() {
    if (!enabled || !awake || stopped || connection || retry !== null) return;
    const current = ++generation;
    function disconnected() {
      if (current !== generation || stopped || !awake) return;
      connection = null;
      retry = schedule(() => {
        retry = null;
        connect();
      }, 5000);
      retry?.unref?.();
    }
    try {
      connection = service.openDesktopAvailability(disconnected);
    } catch (_error) {
      disconnected(); // Daemon/token not ready: retry only the connection.
    }
  }

  powerMonitor.on("suspend", () => {
    awake = false;
    close();
  });
  powerMonitor.on("resume", () => {
    awake = true;
    connect();
  });
  app.on("before-quit", () => {
    stopped = true;
    close();
  });
  return {
    start() {
      enabled = true;
      connect();
    }
  };
}

module.exports = { createAvailabilityController };
