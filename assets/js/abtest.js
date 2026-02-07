const AB_VARIANT_KEY = "casesVariant";

const redirectToVariant = () => {
  const savedVariant = localStorage.getItem(AB_VARIANT_KEY);
  const variant = savedVariant || (Math.random() < 0.5 ? "a" : "b");
  localStorage.setItem(AB_VARIANT_KEY, variant);

  const target = variant === "a" ? "cases-a.html" : "cases-b.html";
  if (!window.location.pathname.endsWith(target)) {
    window.location.replace(target);
  }
};

document.addEventListener("DOMContentLoaded", () => {
  if (window.location.pathname.endsWith("/cases.html")) {
    redirectToVariant();
  }
});
