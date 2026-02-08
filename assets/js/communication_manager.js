const syncButton = document.querySelector("[data-comm-sync]");
const syncStatus = document.querySelector("[data-comm-status]");

if (syncButton) {
  syncButton.addEventListener("click", async () => {
    if (!syncStatus) {
      return;
    }
    syncStatus.textContent = "Syncing communication queues...";
    try {
      const response = await fetch("/api/admin/communications/sync", { method: "POST" });
      if (!response.ok) {
        throw new Error("Sync failed");
      }
      const result = await response.json();
      syncStatus.textContent = result.message;
    } catch (error) {
      syncStatus.textContent = "Unable to sync communications.";
    }
  });
}
