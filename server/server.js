const express = require("express");
const cors = require("cors");
const fs = require("fs");
const path = require("path");
require("dotenv").config();

const app = express();
const port = process.env.PORT || 3000;

const queuePath = path.join(__dirname, "queue.json");
const statsPath = path.join(__dirname, "stats.json");

app.use(cors());
app.use(express.json());

const isValidEmail = (email) => /\S+@\S+\.\S+/.test(email);

const readJsonFile = (filePath, fallback) => {
  try {
    if (fs.existsSync(filePath)) {
      return JSON.parse(fs.readFileSync(filePath, "utf-8"));
    }
  } catch (error) {
    return fallback;
  }
  return fallback;
};

const writeJsonFile = (filePath, data) => {
  fs.writeFileSync(filePath, JSON.stringify(data, null, 2));
};

const updateStats = (updater) => {
  const stats = readJsonFile(statsPath, {
    totalLeads: 0,
    hotLeads: 0,
    visitsToPayment: 0
  });
  updater(stats);
  writeJsonFile(statsPath, stats);
};

const sendTelegramMessage = async ({ name, email, message, service, type, leadScore }) => {
  const { BOT_TOKEN, CHAT_ID } = process.env;

  if (!BOT_TOKEN || !CHAT_ID) {
    throw new Error("Telegram config missing.");
  }

  if (!name || !email || !isValidEmail(email)) {
    throw new Error("Invalid contact data.");
  }

  const numericScore = Number(leadScore) || 0;
  let leadLabel = "[ℹ COLD LEAD]";
  if (numericScore > 50) {
    leadLabel = "[🔥 HOT LEAD]";
  } else if (numericScore > 30) {
    leadLabel = "[⚠ WARM LEAD]";
  }

  const text = [
    `${leadLabel} <b>New Aegis Sentinel request</b>`,
    `<b>Type:</b> ${type || "contact"}`,
    `<b>Name:</b> ${name}`,
    `<b>Email:</b> ${email}`,
    `<b>Service:</b> ${service || "N/A"}`,
    `<b>Message:</b> ${message || ""}`,
    `<b>Lead Score:</b> ${numericScore}`
  ].join("\n");

  const response = await fetch(`https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: CHAT_ID,
      text,
      parse_mode: "HTML"
    })
  });

  if (!response.ok) {
    throw new Error("Telegram message failed.");
  }
};

app.post("/api/contact", async (req, res) => {
  const { name, email, message, service, type, leadScore } = req.body;

  try {
    await sendTelegramMessage({ name, email, message, service, type, leadScore });

    updateStats((stats) => {
      stats.totalLeads += 1;
      if (Number(leadScore) > 50) {
        stats.hotLeads += 1;
      }
    });

    return res.json({ message: "Contact request sent to Telegram." });
  } catch (error) {
    const status = error.message === "Invalid contact data." ? 400 : 500;
    return res.status(status).json({ message: error.message });
  }
});

app.post("/api/telegram", async (req, res) => {
  const { name, email, message, service, type, leadScore } = req.body;
  try {
    await sendTelegramMessage({ name, email, message, service, type, leadScore });
    return res.json({ message: "Telegram sent." });
  } catch (error) {
    const status = error.message === "Invalid contact data." ? 400 : 500;
    return res.status(status).json({ message: error.message });
  }
});

app.post("/api/track-payment", (req, res) => {
  updateStats((stats) => {
    stats.visitsToPayment += 1;
  });
  res.json({ message: "Payment visit tracked." });
});

app.get("/api/stats", (req, res) => {
  const stats = readJsonFile(statsPath, {
    totalLeads: 0,
    hotLeads: 0,
    visitsToPayment: 0
  });
  res.json(stats);
});

app.get("/api/process-queue", async (req, res) => {
  return res.json({
    processed: 0,
    remaining: readJsonFile(queuePath, []).length,
    message: "Email follow-up queue is disabled (SMTP removed)."
  });
});

app.post("/api/create-payment-intent", (req, res) => {
  return res.status(501).json({ message: "Payments are disabled in demo mode." });
});

app.listen(port, () => {
  console.log(`Aegis Sentinel server listening on port ${port}`);
});
