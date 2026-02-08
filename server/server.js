const express = require("express");
const cors = require("cors");
const fs = require("fs");
const path = require("path");
require("dotenv").config();

const app = express();
const port = process.env.PORT || 3000;

const queuePath = path.join(__dirname, "queue.json");
const statsPath = path.join(__dirname, "stats.json");
const dataDir = path.join(__dirname, "data");
const analysisPath = path.join(dataDir, "analysis_reports.json");
const briefsPath = path.join(dataDir, "educational_briefs.json");
const consentsPath = path.join(dataDir, "consents.json");
const demosPath = path.join(dataDir, "demo_sessions.json");
const agentsPath = path.join(dataDir, "metrics_agents.json");
const commsPath = path.join(dataDir, "communications_log.json");
const usersPath = path.join(dataDir, "portal_users.json");
const nowPaymentsKey = process.env.NOWPAYMENTS_API_KEY;
const nowPaymentsPublicKey = process.env.NOWPAYMENTS_PUBLIC_KEY;

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

const ensureDataDir = () => {
  if (!fs.existsSync(dataDir)) {
    fs.mkdirSync(dataDir, { recursive: true });
  }
};

const appendJsonEntry = (filePath, entry) => {
  ensureDataDir();
  const items = readJsonFile(filePath, []);
  items.push(entry);
  writeJsonFile(filePath, items);
  return items;
};

const generatePassword = (length = 12) => {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
  let password = "";
  for (let i = 0; i < length; i += 1) {
    password += alphabet[Math.floor(Math.random() * alphabet.length)];
  }
  return password;
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

app.post("/api/nowpayments/create-payment", async (req, res) => {
  const { product, amount, email, payCurrency } = req.body;
  if (!nowPaymentsKey || !nowPaymentsPublicKey) {
    return res.status(500).json({ message: "NowPayments not configured." });
  }
  if (!amount || !email || !isValidEmail(email)) {
    return res.status(400).json({ message: "Valid email and amount are required." });
  }

  try {
    const response = await fetch("https://api.nowpayments.io/v1/payment", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": nowPaymentsKey
      },
      body: JSON.stringify({
        price_amount: Number(amount).toFixed(2),
        price_currency: "usd",
        pay_currency: payCurrency || "btc",
        order_id: `order_${Date.now()}`,
        order_description: product || "Aegis Sentinel report",
        ipn_callback_url: process.env.NOWPAYMENTS_IPN_URL || "",
        success_url: process.env.NOWPAYMENTS_SUCCESS_URL || "",
        cancel_url: process.env.NOWPAYMENTS_CANCEL_URL || ""
      })
    });

    if (!response.ok) {
      const errorPayload = await response.json().catch(() => ({}));
      return res.status(500).json({ message: errorPayload.message || "Payment initialization failed." });
    }

    const data = await response.json();
    return res.json({
      paymentUrl: data.payment_url,
      paymentId: data.payment_id
    });
  } catch (error) {
    return res.status(500).json({ message: "Payment initialization failed." });
  }
});

app.post("/api/client-data-analysis", (req, res) => {
  const { clientName, email, consent, payload, summary } = req.body;
  if (!consent || !clientName || !email || !payload) {
    return res.status(400).json({ message: "Consent and data are required." });
  }

  const report = {
    id: `report_${Date.now()}`,
    clientName,
    email,
    summary: summary || "Client provided structured data for analysis.",
    createdAt: new Date().toISOString()
  };

  appendJsonEntry(analysisPath, report);
  return res.json({
    message: "Analysis complete. Report generated.",
    summary: report.summary,
    reportId: report.id
  });
});

app.post("/api/generate-educational-brief", (req, res) => {
  const { reportId, email, consent } = req.body;
  if (!consent || !reportId || !email) {
    return res.status(400).json({ message: "Consent, report ID, and email are required." });
  }

  const brief = {
    id: `brief_${Date.now()}`,
    reportId,
    email,
    createdAt: new Date().toISOString(),
    status: "generated"
  };

  appendJsonEntry(briefsPath, brief);
  appendJsonEntry(commsPath, {
    id: `comm_${Date.now()}`,
    type: "educational_brief",
    email,
    reportId,
    status: "ready",
    createdAt: new Date().toISOString()
  });

  return res.json({ message: "Educational brief generated and queued for delivery." });
});

app.post("/api/metrics-consent", (req, res) => {
  const { email, metrics, consent } = req.body;
  if (!consent || !email) {
    return res.status(400).json({ message: "Consent and email are required." });
  }

  appendJsonEntry(consentsPath, {
    id: `consent_${Date.now()}`,
    email,
    metrics: metrics || [],
    createdAt: new Date().toISOString()
  });
  appendJsonEntry(agentsPath, {
    id: `agent_${Date.now()}`,
    email,
    metrics: metrics || [],
    status: "opted-in",
    updatedAt: new Date().toISOString()
  });

  return res.json({ message: "Metrics consent stored." });
});

app.get("/api/admin/analysis-reports", (req, res) => {
  const reports = readJsonFile(analysisPath, []);
  res.json({
    items: reports.map((report) => ({
      title: report.clientName,
      body: report.summary,
      meta: report.createdAt
    }))
  });
});

app.get("/api/admin/demo-sessions", (req, res) => {
  const demos = readJsonFile(demosPath, []);
  res.json({
    items: demos.map((demo) => ({
      title: demo.name || "Demo session",
      body: demo.status || "scheduled",
      meta: demo.createdAt
    }))
  });
});

app.get("/api/admin/metrics-agents", (req, res) => {
  const agents = readJsonFile(agentsPath, []);
  res.json({
    items: agents.map((agent) => ({
      title: agent.email,
      body: `Metrics: ${(agent.metrics || []).join(", ") || "none"}`,
      meta: agent.status
    }))
  });
});

app.get("/api/admin/consents", (req, res) => {
  const consents = readJsonFile(consentsPath, []);
  res.json({
    items: consents.map((consent) => ({
      title: consent.email,
      body: `Consented metrics: ${(consent.metrics || []).join(", ") || "none"}`,
      meta: consent.createdAt
    }))
  });
});

app.get("/api/admin/communications", (req, res) => {
  const comms = readJsonFile(commsPath, []);
  res.json({
    items: comms.map((comm) => ({
      title: comm.type,
      body: `Email: ${comm.email}`,
      meta: comm.status
    }))
  });
});

app.post("/api/admin/communications/sync", (req, res) => {
  appendJsonEntry(commsPath, {
    id: `comm_${Date.now()}`,
    type: "sync",
    email: "system",
    status: "synced",
    createdAt: new Date().toISOString()
  });
  res.json({ message: "Communication workflows synced." });
});

app.get("/api/admin/analytics", (req, res) => {
  const reports = readJsonFile(analysisPath, []);
  const consents = readJsonFile(consentsPath, []);
  const demos = readJsonFile(demosPath, []);
  res.json({
    reportsGenerated: reports.length,
    activeConsents: consents.length,
    demoSessions: demos.length
  });
});

app.post("/api/admin/create-user", (req, res) => {
  const { email } = req.body;
  if (!email || !isValidEmail(email)) {
    return res.status(400).json({ message: "Valid email is required." });
  }

  const users = readJsonFile(usersPath, []);
  const exists = users.find((user) => user.email === email);
  if (exists) {
    return res.status(409).json({ message: "User already exists." });
  }

  const password = generatePassword(12);
  const user = {
    id: `user_${Date.now()}`,
    email,
    password,
    createdAt: new Date().toISOString(),
    subscription: { status: "Trial", nextRenewal: "Pending activation" },
    security: {
      attacks: "No attacks recorded",
      uptime: "Monitoring enabled",
      latestReport: "Ready for review"
    }
  };

  appendJsonEntry(usersPath, user);
  return res.json({ message: "User created.", password });
});

app.get("/api/admin/users", (req, res) => {
  const users = readJsonFile(usersPath, []);
  res.json({
    items: users.map((user) => ({
      title: user.email,
      body: `Status: ${user.subscription?.status || "Active"}`,
      meta: user.createdAt
    }))
  });
});

app.post("/api/portal/login", (req, res) => {
  const { email, password } = req.body;
  if (!email || !password) {
    return res.status(400).json({ message: "Email and password are required." });
  }
  const users = readJsonFile(usersPath, []);
  const user = users.find((entry) => entry.email === email && entry.password === password);
  if (!user) {
    return res.status(401).json({ message: "Invalid credentials." });
  }
  res.json({
    subscription: user.subscription,
    security: user.security
  });
});

app.listen(port, () => {
  console.log(`Aegis Sentinel server listening on port ${port}`);
});
