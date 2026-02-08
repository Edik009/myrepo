const analyticsOutput = document.querySelector("[data-internal-analytics]");

const renderAnalytics = (metrics) => {
  if (!analyticsOutput) {
    return;
  }
  analyticsOutput.innerHTML = `
    <div class="card-grid">
      <div class="card">
        <h3>Reports generated</h3>
        <p>${metrics.reportsGenerated ?? 0}</p>
      </div>
      <div class="card">
        <h3>Active consents</h3>
        <p>${metrics.activeConsents ?? 0}</p>
      </div>
      <div class="card">
        <h3>Demo sessions</h3>
        <p>${metrics.demoSessions ?? 0}</p>
      </div>
    </div>
  `;
};

const fetchAnalytics = async () => {
  const response = await fetch("/api/admin/analytics");
  if (!response.ok) {
    throw new Error("Analytics fetch failed");
  }
  return response.json();
};

if (analyticsOutput) {
  fetchAnalytics()
    .then(renderAnalytics)
    .catch(() => renderAnalytics({}));
}
