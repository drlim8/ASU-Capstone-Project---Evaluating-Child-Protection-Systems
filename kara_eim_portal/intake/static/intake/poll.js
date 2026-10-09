// Polls the batch status endpoint while documents are processing, then reloads once.
(function () {
  var script = document.currentScript;
  var url = script && script.getAttribute("data-status-url");
  if (!url) { return; }

  var STATUS_LABELS = {
    queued: "Queued", fetching: "Fetching", normalizing: "Normalizing",
    ready: "Ready", rejected: "Rejected", failed: "Failed"
  };

  function update(data) {
    data.documents.forEach(function (doc) {
      var row = document.querySelector('tr[data-doc-id="' + doc.id + '"]');
      if (!row) { return; }
      ["pages", "tables", "images", "warnings"].forEach(function (name) {
        var cell = row.querySelector('[data-field="' + name + '"]');
        if (cell) { cell.textContent = doc[name]; }
      });
      var badge = row.querySelector('[data-field="status"]');
      if (badge) {
        badge.textContent = STATUS_LABELS[doc.status] || doc.status;
        badge.className = "status status-" + doc.status;
      }
    });
  }

  function poll() {
    fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (response) {
        if (!response.ok) { throw new Error("HTTP " + response.status); }
        return response.json();
      })
      .then(function (data) {
        if (data.status === "processing") {
          update(data);
          setTimeout(poll, 3000);
        } else {
          window.location.reload();
        }
      })
      .catch(function () { setTimeout(poll, 3000); });
  }

  setTimeout(poll, 3000);
})();
