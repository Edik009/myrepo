const metricsForm = document.querySelector("[data-metrics-consent-form]");
const metricsStatus = document.querySelector("[data-metrics-status]");

if (metricsForm) {
  metricsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!metricsStatus) {
      return;
    }

    const formData = new FormData(metricsForm);
    const consent = formData.get("metrics_consent") === "on";
    const email = formData.get("metrics_email");
    const metrics = formData.getAll("metrics");

    if (!consent) {
      metricsStatus.textContent = "Consent is required to enable metrics collection.";
      return;
    }

    try {
      const response = await fetch("/api/metrics-consent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, metrics, consent })
      });

      if (!response.ok) {
        throw new Error("Metrics request failed");
      }

      metricsStatus.textContent = "Metrics collection preferences saved.";
    } catch (error) {
      metricsStatus.textContent = "Unable to save metrics preferences.";
    }
  });
}
