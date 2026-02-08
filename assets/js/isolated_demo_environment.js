const demoButtons = document.querySelectorAll("[data-demo-action]");
const demoLog = document.querySelector("[data-demo-log]");

const appendLog = (message) => {
  if (!demoLog) {
    return;
  }
  const line = document.createElement("div");
  line.textContent = message;
  demoLog.appendChild(line);
};

const runDemo = (type) => {
  const timestamp = new Date().toLocaleString();
  appendLog(`[${timestamp}] Demo started: ${type}.`);
  setTimeout(() => {
    appendLog(`[${timestamp}] Demo completed: ${type}. Results stored in demo sandbox.`);
  }, 1200);
};

demoButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const type = button.dataset.demoAction;
    runDemo(type);
  });
});
