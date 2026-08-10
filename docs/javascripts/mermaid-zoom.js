/* Fullscreen pan/zoom viewer for Mermaid diagrams.
 * Click any diagram to open it fullscreen; scroll to zoom, drag to pan,
 * Esc / click backdrop / X to close. No dependencies; delegated handler so
 * it survives Material's instant navigation and async Mermaid rendering. */
(function () {
  "use strict";

  var overlay = null;

  function closeOverlay() {
    if (overlay) {
      overlay.remove();
      overlay = null;
      document.removeEventListener("keydown", onKey);
    }
  }

  function onKey(e) {
    if (e.key === "Escape") closeOverlay();
  }

  function openOverlay(svg) {
    closeOverlay();
    overlay = document.createElement("div");
    overlay.className = "mermaid-zoom-overlay";

    var stage = document.createElement("div");
    stage.className = "mermaid-zoom-stage";

    var clone = svg.cloneNode(true);
    clone.removeAttribute("width");
    clone.removeAttribute("height");
    clone.style.maxWidth = "none";
    clone.style.width = "100%";
    clone.style.height = "100%";

    var closeBtn = document.createElement("button");
    closeBtn.className = "mermaid-zoom-close";
    closeBtn.textContent = "×";
    closeBtn.setAttribute("aria-label", "Close");
    closeBtn.addEventListener("click", closeOverlay);

    var hint = document.createElement("div");
    hint.className = "mermaid-zoom-hint";
    hint.textContent = "scroll: zoom · drag: pan · esc: close";

    stage.appendChild(clone);
    overlay.appendChild(stage);
    overlay.appendChild(closeBtn);
    overlay.appendChild(hint);
    document.body.appendChild(overlay);
    document.addEventListener("keydown", onKey);

    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) closeOverlay();
    });

    /* pan/zoom state applied as a CSS transform on the stage */
    var scale = 1, tx = 0, ty = 0;
    function apply() {
      stage.style.transform =
        "translate(" + tx + "px," + ty + "px) scale(" + scale + ")";
    }

    overlay.addEventListener("wheel", function (e) {
      e.preventDefault();
      var factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      var next = Math.min(12, Math.max(0.2, scale * factor));
      /* zoom towards the cursor */
      var rect = overlay.getBoundingClientRect();
      var cx = e.clientX - rect.left - rect.width / 2;
      var cy = e.clientY - rect.top - rect.height / 2;
      tx = cx - (cx - tx) * (next / scale);
      ty = cy - (cy - ty) * (next / scale);
      scale = next;
      apply();
    }, { passive: false });

    var dragging = false, lastX = 0, lastY = 0;
    overlay.addEventListener("pointerdown", function (e) {
      if (e.target === closeBtn) return;
      dragging = true;
      lastX = e.clientX;
      lastY = e.clientY;
      overlay.setPointerCapture(e.pointerId);
      overlay.classList.add("dragging");
    });
    overlay.addEventListener("pointermove", function (e) {
      if (!dragging) return;
      tx += e.clientX - lastX;
      ty += e.clientY - lastY;
      lastX = e.clientX;
      lastY = e.clientY;
      apply();
    });
    overlay.addEventListener("pointerup", function () {
      dragging = false;
      overlay.classList.remove("dragging");
    });
  }

  document.addEventListener("click", function (e) {
    if (overlay) return;
    var svg = e.target.closest && e.target.closest(".mermaid svg, pre.mermaid svg");
    if (svg) openOverlay(svg);
  });
})();
