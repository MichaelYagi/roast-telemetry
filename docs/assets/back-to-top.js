// Floating "back to top" button -- hidden until the page has been
// scrolled down some, then pinned bottom-right for the rest of the
// scroll. Injects its own markup so a page only needs one <script> tag,
// nothing to duplicate/keep in sync in the HTML itself.
(function () {
  var SHOW_AFTER_PX = 400;

  var btn = document.createElement("a");
  btn.href = "#";
  btn.className = "back-to-top";
  btn.setAttribute("aria-label", "Back to top");
  btn.textContent = "↑";
  btn.addEventListener("click", function (e) {
    e.preventDefault();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
  document.body.appendChild(btn);

  function onScroll() {
    btn.classList.toggle("visible", window.scrollY > SHOW_AFTER_PX);
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
})();
