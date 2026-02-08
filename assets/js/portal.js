const loginForm = document.querySelector("[data-portal-login]");
const dashboard = document.querySelector("[data-portal-dashboard]");
const logoutButton = document.querySelector("[data-portal-logout]");
const loginStatus = document.querySelector("[data-portal-login-status]");
const subscriptionStatus = document.querySelector("[data-portal-subscription]");
const renewalStatus = document.querySelector("[data-portal-renewal]");
const attacksStatus = document.querySelector("[data-portal-attacks]");
const uptimeStatus = document.querySelector("[data-portal-uptime]");
const reportStatus = document.querySelector("[data-portal-latest-report]");
const manageButton = document.querySelector("[data-portal-manage]");

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
    if (loginStatus) {
      loginStatus.textContent = "Signing in...";
    }

    const formData = new FormData(loginForm);
    const email = formData.get("email");
    const password = formData.get("password");

    fetch("/api/portal/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password })
    })
      .then((response) => {
        if (!response.ok) {
          throw new Error("Login failed");
        }
        return response.json();
      })
      .then((data) => {
        toggleDashboard(true);
        if (loginStatus) {
          loginStatus.textContent = "";
        }
        if (subscriptionStatus) {
          subscriptionStatus.textContent = `Status: ${data.subscription?.status || "Active"}`;
        }
        if (renewalStatus) {
          renewalStatus.textContent = data.subscription?.nextRenewal || "Pending activation";
        }
        if (attacksStatus) {
          attacksStatus.textContent = data.security?.attacks || "No attacks recorded";
        }
        if (uptimeStatus) {
          uptimeStatus.textContent = data.security?.uptime || "Monitoring enabled";
        }
        if (reportStatus) {
          reportStatus.textContent = data.security?.latestReport || "Ready for review";
        }
      })
      .catch(() => {
        if (loginStatus) {
          loginStatus.textContent = "Invalid credentials. Please try again.";
        }
      });
  });
}

if (logoutButton) {
  logoutButton.addEventListener("click", () => {
    toggleDashboard(false);
  });
}

if (manageButton) {
  manageButton.addEventListener("click", () => {
    if (loginStatus) {
      loginStatus.textContent = "Subscription management will be available soon.";
    }
  });
}
