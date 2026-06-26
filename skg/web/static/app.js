// Minimal UI glue (no framework): keep the transcript scrolled to the latest
// answer, and clear/refocus the input after each question is sent.
document.body.addEventListener("htmx:afterSwap", function () {
  const t = document.getElementById("transcript");
  if (t) t.scrollTop = t.scrollHeight;
});

// Matrix view: clicking a cell entry toggles its full claim card(s) inline as
// a new full-width row directly below the clicked evidence row (so the detail
// never drops below the fold). Pre-rendered cards live hidden in the same
// .matrix-wrap; we clone them in rather than round-trip the server.
document.body.addEventListener("click", function (ev) {
  const btn = ev.target.closest(".cell-entry");
  if (!btn) return;
  const wrap = btn.closest(".matrix-wrap");
  if (!wrap) return;

  const id = btn.dataset.detailId;
  const row = btn.closest("tr");
  const tbody = row.parentNode;
  const cols = btn.closest("table").querySelector("tr").children.length;

  const open = tbody.querySelector('tr.detail-row[data-for="' + id + '"]');
  if (open) {
    open.remove();
    btn.setAttribute("aria-expanded", "false");
    return;
  }

  const src = wrap.querySelector('.claim-detail[data-detail-id="' + id + '"]');
  if (!src) return;

  const tr = document.createElement("tr");
  tr.className = "detail-row";
  tr.dataset.for = id;
  const td = document.createElement("td");
  td.colSpan = cols;
  td.innerHTML = src.innerHTML;
  tr.appendChild(td);

  // Insert below this row, after any detail rows it already owns.
  let anchor = row;
  while (anchor.nextElementSibling &&
         anchor.nextElementSibling.classList.contains("detail-row")) {
    anchor = anchor.nextElementSibling;
  }
  anchor.after(tr);
  btn.setAttribute("aria-expanded", "true");
});

document.getElementById("ask-form").addEventListener("htmx:afterRequest", function () {
  const q = document.getElementById("q");
  if (q) {
    q.value = "";
    q.focus();
  }
});
