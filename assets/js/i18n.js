const defaultLang = "en";
const rtlLanguages = new Set(["ar"]);

const resolveBasePath = () => (window.location.pathname.includes("/pages/") ? "../" : "./");

const loadTranslations = async (lang) => {
  if (window.TRANSLATIONS && window.TRANSLATIONS[lang]) {
    return window.TRANSLATIONS[lang];
  }
  const response = await fetch(`${resolveBasePath()}assets/locales/${lang}.json`);
  if (!response.ok) {
    throw new Error("Translation file not found");
  }
  return response.json();
};

const applyTranslations = (translations, lang) => {
  document.querySelectorAll("[data-i18n]").forEach((element) => {
    const key = element.getAttribute("data-i18n");
    if (translations[key]) {
      element.textContent = translations[key];
    }
  });

  document.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
    const key = element.getAttribute("data-i18n-placeholder");
    if (translations[key]) {
      element.setAttribute("placeholder", translations[key]);
    }
  });

  document.querySelectorAll("[data-i18n-value]").forEach((element) => {
    const key = element.getAttribute("data-i18n-value");
    if (translations[key]) {
      element.value = translations[key];
    }
  });

  const html = document.documentElement;
  html.setAttribute("lang", lang);
  if (rtlLanguages.has(lang)) {
    html.setAttribute("dir", "rtl");
    document.body.classList.add("rtl");
  } else {
    html.setAttribute("dir", "ltr");
    document.body.classList.remove("rtl");
  }

  if (typeof window.applyGeoTargeting === "function") {
    window.applyGeoTargeting();
  }
};

const setLanguage = async (lang) => {
  try {
    const translations = await loadTranslations(lang);
    applyTranslations(translations, lang);
    localStorage.setItem("language", lang);
  } catch (error) {
    if (lang !== defaultLang) {
      setLanguage(defaultLang);
    }
  }
};

const initLanguage = () => {
  const languageSelector = document.querySelector("[data-language-selector]");
  const storedLang = localStorage.getItem("language") || defaultLang;
  if (languageSelector) {
    languageSelector.value = storedLang;
    languageSelector.addEventListener("change", (event) => {
      setLanguage(event.target.value);
    });
  }
  setLanguage(storedLang);
};

document.addEventListener("DOMContentLoaded", initLanguage);
