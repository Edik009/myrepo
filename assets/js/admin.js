const ADMIN_PASSWORD = "AegisSentinel2024";

const loginForm = document.querySelector("[data-admin-login]");
const dashboard = document.querySelector("[data-admin-dashboard]");
const errorMessage = document.querySelector("[data-admin-error]");

const renderStats = (stats) => {
  if (!dashboard) {
    return;
  }
  dashboard.innerHTML = `
    <div class="card-grid">
      <div class="card">
        <h3>Total Leads</h3>
        <p>${stats.totalLeads ?? 0}</p>
      </div>
      <div class="card">
        <h3>Hot Leads</h3>
        <p>${stats.hotLeads ?? 0}</p>
      </div>
      <div class="card">
        <h3>Visits To Payment</h3>
        <p>${stats.visitsToPayment ?? 0}</p>
      </div>
    </div>
  `;
};

const fetchStats = async () => {
  const response = await fetch("/api/stats");
  if (!response.ok) {
    throw new Error("Stats fetch failed");
  }
  return response.json();
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
      const stats = await fetchStats();
      renderStats(stats);
    } catch (error) {
      renderStats({});
    }
  });
}
