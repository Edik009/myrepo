const initCertificates = () => {
  const items = Array.from(document.querySelectorAll("[data-cert-item]"));
  if (items.length === 0) {
    return;
  }

  let lightbox = document.querySelector(".cert-lightbox");
  if (!lightbox) {
    lightbox = document.createElement("div");
    lightbox.className = "cert-lightbox";
    lightbox.innerHTML = `
      <div class="cert-lightbox-backdrop" data-cert-close></div>
      <div class="cert-lightbox-content" role="dialog" aria-modal="true">
        <button class="cert-lightbox-close" type="button" data-cert-close aria-label="Close">×</button>
        <button class="cert-lightbox-nav cert-lightbox-prev" type="button" data-cert-prev aria-label="Previous">‹</button>
        <img class="cert-lightbox-image" alt="" />
        <button class="cert-lightbox-nav cert-lightbox-next" type="button" data-cert-next aria-label="Next">›</button>
        <p class="cert-lightbox-caption"></p>
      </div>
    `;
    document.body.appendChild(lightbox);
  }

  const imageEl = lightbox.querySelector(".cert-lightbox-image");
  const captionEl = lightbox.querySelector(".cert-lightbox-caption");
  const closeButtons = lightbox.querySelectorAll("[data-cert-close]");
  const prevButton = lightbox.querySelector("[data-cert-prev]");
  const nextButton = lightbox.querySelector("[data-cert-next]");

  let visibleItems = items;
  let activeIndex = 0;

  const openLightbox = (index) => {
    activeIndex = index;
    const target = visibleItems[activeIndex];
    if (!target) {
      return;
    }
    imageEl.src = target.dataset.certFull || target.getAttribute("href") || "";
    imageEl.alt = target.dataset.certAlt || target.dataset.certCaption || "";
    captionEl.textContent = target.dataset.certCaption || "";
    lightbox.classList.add("open");
    document.body.style.overflow = "hidden";
  };

  const closeLightbox = () => {
    lightbox.classList.remove("open");
    document.body.style.overflow = "";
  };

  const showNext = () => {
    if (!visibleItems.length) return;
    activeIndex = (activeIndex + 1) % visibleItems.length;
    openLightbox(activeIndex);
  };

  const showPrev = () => {
    if (!visibleItems.length) return;
    activeIndex = (activeIndex - 1 + visibleItems.length) % visibleItems.length;
    openLightbox(activeIndex);
  };

  items.forEach((item, index) => {
    item.addEventListener("click", (event) => {
      event.preventDefault();
      visibleItems = items.filter((el) => !el.classList.contains("is-hidden"));
      const targetIndex = visibleItems.indexOf(item);
      openLightbox(targetIndex === -1 ? index : targetIndex);
    });
  });

  closeButtons.forEach((button) => {
    button.addEventListener("click", closeLightbox);
  });

  prevButton?.addEventListener("click", showPrev);
  nextButton?.addEventListener("click", showNext);

  document.addEventListener("keydown", (event) => {
    if (!lightbox.classList.contains("open")) return;
    if (event.key === "Escape") closeLightbox();
    if (event.key === "ArrowRight") showNext();
    if (event.key === "ArrowLeft") showPrev();
  });

  document.querySelectorAll("[data-cert-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      const filter = button.dataset.certFilter;
      document.querySelectorAll("[data-cert-filter]").forEach((btn) => {
        btn.classList.toggle("active", btn === button);
      });
      items.forEach((item) => {
        const category = item.dataset.certCategory || "all";
        const shouldShow = filter === "all" || category.split(",").includes(filter);
        item.classList.toggle("is-hidden", !shouldShow);
      });
    });
  });
};

document.addEventListener("DOMContentLoaded", initCertificates);
