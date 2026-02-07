const analysisForm = document.querySelector("[data-client-data-form]");
const analysisStatus = document.querySelector("[data-client-data-status]");
const analysisOutput = document.querySelector("[data-client-data-output]");
const briefButton = document.querySelector("[data-brief-generate]");
const briefStatus = document.querySelector("[data-brief-status]");
let latestReportId = null;

const summarizeData = (payload) => {
  if (!payload || typeof payload !== "object") {
    return "No structured data detected.";
  }
  const keys = Object.keys(payload);
  return `Received ${keys.length} top-level fields: ${keys.slice(0, 6).join(", ")}${keys.length > 6 ? ", ..." : ""}.`;
};

if (analysisForm) {
  analysisForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!analysisStatus || !analysisOutput) {
      return;
    }

    analysisStatus.textContent = "Analyzing provided data...";
    analysisOutput.textContent = "";

    const formData = new FormData(analysisForm);
    const consent = formData.get("consent") === "on";
    const file = formData.get("data_file");
    const email = formData.get("email");
    const clientName = formData.get("client_name");

    if (!consent) {
      analysisStatus.textContent = "Consent is required to process your data.";
      return;
    }

    if (!file || typeof file === "string") {
      analysisStatus.textContent = "Please upload a JSON file exported from your scan.";
      return;
    }

    try {
      const text = await file.text();
      const payload = JSON.parse(text);
      const response = await fetch("/api/client-data-analysis", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          clientName,
          email,
          consent,
          payload,
          summary: summarizeData(payload)
        })
      });

      if (!response.ok) {
        throw new Error("Analysis request failed");
      }

      const result = await response.json();
      latestReportId = result.reportId || null;
      analysisStatus.textContent = result.message;
      analysisOutput.textContent = result.summary || summarizeData(payload);
    } catch (error) {
      analysisStatus.textContent = "Unable to analyze the file. Please verify JSON format.";
    }
  });
}

if (briefButton) {
  briefButton.addEventListener("click", async () => {
    if (!briefStatus) {
      return;
    }
    if (!latestReportId) {
      briefStatus.textContent = "Run analysis first to generate a brief.";
      return;
    }

    briefStatus.textContent = "Generating educational brief...";
    const emailField = analysisForm?.querySelector("input[name='email']");
    const consentField = analysisForm?.querySelector("input[name='consent']");
    const email = emailField?.value || "";
    const consent = consentField?.checked;

    try {
      const response = await fetch("/api/generate-educational-brief", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reportId: latestReportId, email, consent })
      });

      if (!response.ok) {
        throw new Error("Brief request failed");
      }

      const result = await response.json();
      briefStatus.textContent = result.message;
    } catch (error) {
      briefStatus.textContent = "Unable to generate the brief. Please try again.";
    }
  });
}
