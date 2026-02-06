const loginForm = document.querySelector("[data-portal-login]");
const dashboard = document.querySelector("[data-portal-dashboard]");
const logoutButton = document.querySelector("[data-portal-logout]");

const toggleDashboard = (show) => {
  if (!dashboard || !loginForm) {
    return;
  }
  dashboard.classList.toggle("active", show);
  loginForm.style.display = show ? "none" : "block";
};

if (loginForm) {
  loginForm.addEventListener("submit", (event) => {
    event.preventDefault();
    toggleDashboard(true);
  });
}

if (logoutButton) {
  logoutButton.addEventListener("click", () => {
    toggleDashboard(false);
  });
}
