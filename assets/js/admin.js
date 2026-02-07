const ADMIN_PASSWORD = "AegisSentinel2024";

const loginForm = document.querySelector("[data-admin-login]");
const dashboard = document.querySelector("[data-admin-dashboard]");
const errorMessage = document.querySelector("[data-admin-error]");

const analysisContainer = document.querySelector("[data-analysis-results]");
const demoContainer = document.querySelector("[data-demo-results]");
const agentContainer = document.querySelector("[data-agent-results]");
const consentContainer = document.querySelector("[data-consent-results]");
const commContainer = document.querySelector("[data-comm-results]");
const refreshButton = document.querySelector("[data-admin-refresh]");

const renderCards = (container, items, emptyMessage) => {
  if (!container) {
    return;
  }
  if (!items || items.length === 0) {
    container.innerHTML = `<div class="card"><p>${emptyMessage}</p></div>`;
    return;
  }
  container.innerHTML = items
    .map(
      (item) => `
        <div class="card">
          <h3>${item.title}</h3>
          <p>${item.body}</p>
          <p>${item.meta || ""}</p>
        </div>
      `
    )
    .join("");
};

const fetchAdminData = async (endpoint) => {
  const response = await fetch(endpoint);
  if (!response.ok) {
    throw new Error("Admin data fetch failed");
  }
  return response.json();
};

const loadAdminData = async () => {
  const [analysis, demos, agents, consents, comms] = await Promise.all([
    fetchAdminData("/api/admin/analysis-reports"),
    fetchAdminData("/api/admin/demo-sessions"),
    fetchAdminData("/api/admin/metrics-agents"),
    fetchAdminData("/api/admin/consents"),
    fetchAdminData("/api/admin/communications")
  ]);

  renderCards(
    analysisContainer,
    analysis.items,
    "No analysis reports yet. Upload client data to generate the first report."
  );
  renderCards(demoContainer, demos.items, "No demo sessions have been scheduled.");
  renderCards(agentContainer, agents.items, "No active agents are connected.");
  renderCards(consentContainer, consents.items, "No consent records found.");
  renderCards(commContainer, comms.items, "No communication events logged.");
};

if (loginForm) {
  loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const password = new FormData(loginForm).get("password");
    if (password !== ADMIN_PASSWORD) {
      if (errorMessage) {
        errorMessage.textContent = "Invalid password.";
      }
      return;
    }

    loginForm.style.display = "none";
    if (errorMessage) {
      errorMessage.textContent = "";
    }

    try {
      await loadAdminData();
    } catch (error) {
      if (dashboard) {
        dashboard.innerHTML = "<p>Unable to load admin data.</p>";
      }
    }
  });
}

if (refreshButton) {
  refreshButton.addEventListener("click", () => {
    loadAdminData().catch(() => {});
  });
}
