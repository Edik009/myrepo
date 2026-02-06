const submitForm = async (form) => {
  const status = form.querySelector("[data-form-status]");
  const payload = Object.fromEntries(new FormData(form).entries());

  try {
    const contactResponse = await fetch("/api/contact", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!contactResponse.ok) {
      throw new Error("Contact request failed");
    }

    const telegramResponse = await fetch("/api/telegram", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!telegramResponse.ok) {
      throw new Error("Telegram request failed");
    }

    status.textContent = status.getAttribute("data-success-text");
    status.style.color = "var(--accent)";
    form.reset();
  } catch (error) {
    status.textContent = status.getAttribute("data-error-text");
    status.style.color = "#f56565";
  }
};

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-contact-form]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      submitForm(form);
    });
  });
});
