// Sticky "on this page" table of contents -- auto-built from this page's
// own <h2>/<h3> headings inside <main>, right side, only shown once the
// viewport is wide enough not to collide with the centered content
// column (see the @media rule in style.css). Injects its own markup
// (like back-to-top.js) so no page has to list its own headings by hand
// and keep them in sync as sections get added/renamed.
(function () {
  var headings = document.querySelectorAll("main > h2, main > h3");
  if (headings.length < 2) return; // not worth a TOC for a short page

  var nav = document.createElement("nav");
  nav.className = "page-toc";
  nav.setAttribute("aria-label", "On this page");

  var list = document.createElement("ul");
  var usedIds = {};

  function slugify(text) {
    return text
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
  }

  var entries = [];
  headings.forEach(function (h) {
    // Reuse an existing id (several headings already have one, and other
    // pages/anchors may already link to it) rather than regenerating --
    // only fill in one where it's missing.
    if (!h.id) {
      var slug = slugify(h.textContent) || "section";
      var unique = slug;
      var n = 2;
      while (usedIds[unique] || document.getElementById(unique)) {
        unique = slug + "-" + n++;
      }
      h.id = unique;
    }
    usedIds[h.id] = true;

    var li = document.createElement("li");
    if (h.tagName === "H3") li.className = "page-toc-sub";
    var a = document.createElement("a");
    a.href = "#" + h.id;
    a.textContent = h.textContent;
    li.appendChild(a);
    list.appendChild(li);
    entries.push({ heading: h, link: a });
  });

  nav.appendChild(list);
  document.body.appendChild(nav);

  // Highlights whichever section is currently under the top of the
  // viewport as you scroll -- a heading counts as "current" once it
  // crosses into a thin band near the top (80px down, see rootMargin),
  // not merely once it's anywhere on screen.
  var observer = new IntersectionObserver(
    function (visibleEntries) {
      visibleEntries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var match = entries.find(function (e) {
          return e.heading === entry.target;
        });
        if (!match) return;
        entries.forEach(function (e) {
          e.link.classList.remove("active");
        });
        match.link.classList.add("active");
      });
    },
    { rootMargin: "-80px 0px -70% 0px" }
  );
  headings.forEach(function (h) {
    observer.observe(h);
  });
})();
