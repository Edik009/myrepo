const getCountryCode = async () => {
  const response = await fetch("https://ipapi.co/json/");
  if (!response.ok) {
    throw new Error("Geo lookup failed");
  }
  const data = await response.json();
  return data.country;
};

const geoTexts = {
  DE: "Strategische Cybersicherheit für den deutschen Mittelstand.",
  AE: "حماية إلكترونية استراتيجية للشركات في الإمارات.",
  FR: "Cybersécurité stratégique pour les organisations françaises.",
  ES: "Ciberseguridad estratégica para empresas en España.",
  US: "Strategic cybersecurity for U.S. enterprises.",
  GB: "Strategic cybersecurity for UK enterprises.",
};

const applyGeoTargeting = async () => {
  const heroTitle = document.querySelector("[data-i18n='hero_title']");
  if (!heroTitle) {
    return;
  }
  const fallbackText = heroTitle.textContent;
  try {
    const country = await getCountryCode();
    if (country && geoTexts[country]) {
      heroTitle.textContent = geoTexts[country];
    } else {
      heroTitle.textContent = fallbackText;
    }
  } catch (error) {
    heroTitle.textContent = fallbackText;
  }
};

window.applyGeoTargeting = applyGeoTargeting;
