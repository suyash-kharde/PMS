(function () {
  "use strict";
  const lightbox = document.getElementById("docsLightbox");
  if (!lightbox) return;
  const lightboxImg = document.getElementById("docsLightboxImg");

  document.querySelectorAll(".docs-shot img").forEach((img) => {
    img.addEventListener("click", () => {
      lightboxImg.src = img.src;
      lightboxImg.alt = img.alt;
      lightbox.classList.add("open");
    });
  });
  lightbox.addEventListener("click", () => lightbox.classList.remove("open"));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") lightbox.classList.remove("open");
  });
})();
