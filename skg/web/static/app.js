// Minimal UI glue (no framework): keep the transcript scrolled to the latest
// answer, and clear/refocus the input after each question is sent.
document.body.addEventListener("htmx:afterSwap", function () {
  const t = document.getElementById("transcript");
  if (t) t.scrollTop = t.scrollHeight;
});

document.getElementById("ask-form").addEventListener("htmx:afterRequest", function () {
  const q = document.getElementById("q");
  if (q) {
    q.value = "";
    q.focus();
  }
});
