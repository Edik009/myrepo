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
const userForm = document.querySelector("[data-admin-user-form]");
const userStatus = document.querySelector("[data-admin-user-status]");
const userPassword = document.querySelector("[data-admin-user-password]");
const userList = document.querySelector("[data-admin-user-list]");

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
  const [analysis, demos, agents, consents, comms, users] = await Promise.all([
    fetchAdminData("/api/admin/analysis-reports"),
    fetchAdminData("/api/admin/demo-sessions"),
    fetchAdminData("/api/admin/metrics-agents"),
    fetchAdminData("/api/admin/consents"),
    fetchAdminData("/api/admin/communications"),
    fetchAdminData("/api/admin/users")
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
  renderCards(userList, users.items, "No portal users created yet.");
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

if (userForm) {
  userForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!userStatus || !userPassword) {
      return;
    }
    userStatus.textContent = "Creating user...";
    userPassword.textContent = "";

    const email = new FormData(userForm).get("email");
    try {
      const response = await fetch("/api/admin/create-user", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email })
      });
      if (!response.ok) {
        throw new Error("Create user failed");
      }
      const result = await response.json();
      userStatus.textContent = result.message;
      userPassword.textContent = `Temporary password: ${result.password}`;
      userForm.reset();
      loadAdminData().catch(() => {});
    } catch (error) {
      userStatus.textContent = "Unable to create user.";
    }
  });
}
