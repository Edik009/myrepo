const express = require("express");
const nodemailer = require("nodemailer");
const cors = require("cors");
const Stripe = require("stripe");
const fs = require("fs");
const path = require("path");
require("dotenv").config();

const app = express();
const port = process.env.PORT || 3000;

const stripeSecret = process.env.STRIPE_SECRET_KEY;
const stripe = stripeSecret ? new Stripe(stripeSecret) : null;

const queuePath = path.join(__dirname, "queue.json");
const statsPath = path.join(__dirname, "stats.json");

app.use(cors());
app.use(express.json());

const isValidEmail = (email) => /\S+@\S+\.\S+/.test(email);

const buildTransport = () => {
  const { SMTP_HOST, SMTP_PORT, SMTP_SECURE, SMTP_USER, SMTP_PASS } = process.env;
  if (!SMTP_HOST || !SMTP_PORT || !SMTP_USER || !SMTP_PASS) {
    return null;
  }
  return nodemailer.createTransport({
    host: SMTP_HOST,
    port: Number(SMTP_PORT),
    secure: SMTP_SECURE === "true",
    auth: {
      user: SMTP_USER,
      pass: SMTP_PASS
    }
  });
};

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

app.post("/api/contact", async (req, res) => {
  const { name, email, message, service, type, leadScore } = req.body;

  if (!name || !email || !isValidEmail(email)) {
    return res.status(400).json({ message: "Invalid contact data." });
  }

  const transporter = buildTransport();
  if (!transporter) {
    return res.status(500).json({ message: "Email transport not configured." });
  }

  const toEmail = process.env.CONTACT_TO || process.env.SMTP_USER;

  try {
    await transporter.sendMail({
      from: `Aegis Sentinel <${process.env.SMTP_USER}>`,
      to: toEmail,
      subject: `Aegis Sentinel ${type || "contact"} request`,
      text: `Name: ${name}\nEmail: ${email}\nService: ${service || "N/A"}\nType: ${type || "N/A"}\nMessage: ${message || ""}\nLead Score: ${leadScore ?? 0}`
    });

    const emailQueue = readJsonFile(queuePath, []);
    emailQueue.push({
      email,
      name,
      service,
      sendAt: Date.now() + 24 * 60 * 60 * 1000
    });
    writeJsonFile(queuePath, emailQueue);

    updateStats((stats) => {
      stats.totalLeads += 1;
      if (Number(leadScore) > 50) {
        stats.hotLeads += 1;
      }
    });

    return res.json({ message: "Contact request sent." });
  } catch (error) {
    return res.status(500).json({ message: "Failed to send email." });
  }
});

app.post("/api/telegram", async (req, res) => {
  const { name, email, message, service, type, leadScore } = req.body;
  const { BOT_TOKEN, CHAT_ID } = process.env;

  if (!BOT_TOKEN || !CHAT_ID) {
    return res.status(500).json({ message: "Telegram config missing." });
  }

  if (!name || !email || !isValidEmail(email)) {
    return res.status(400).json({ message: "Invalid contact data." });
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

  try {
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
      return res.status(500).json({ message: "Telegram message failed." });
    }

    return res.json({ message: "Telegram sent." });
  } catch (error) {
    return res.status(500).json({ message: "Telegram request failed." });
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
  const transporter = buildTransport();
  if (!transporter) {
    return res.status(500).json({ message: "Email transport not configured." });
  }

  const queue = readJsonFile(queuePath, []);
  const now = Date.now();
  const ready = queue.filter((job) => job.sendAt <= now);
  const pending = queue.filter((job) => job.sendAt > now);

  for (const job of ready) {
    const subject = `Follow-up: Additional insights on ${job.service || "your security program"}`;
    const text = `Hello ${job.name || "there"},\n\nWe wanted to share additional insights on ${job.service || "your security program"}. Our team can provide a tailored briefing whenever you're ready.\n\nRegards,\nAegis Sentinel`;

    try {
      await transporter.sendMail({
        from: `Aegis Sentinel <${process.env.SMTP_USER}>`,
        to: job.email,
        subject,
        text
      });
    } catch (error) {
      // Skip failures and keep pending queue intact.
      pending.push(job);
    }
  }

  writeJsonFile(queuePath, pending);
  return res.json({ processed: ready.length, remaining: pending.length });
});

app.post("/api/create-payment-intent", async (req, res) => {
  const { email, product } = req.body;

  if (!stripe) {
    return res.status(500).json({ message: "Stripe not configured." });
  }

  if (!email || !isValidEmail(email)) {
    return res.status(400).json({ message: "Valid email is required." });
  }

  const products = {
    basic: { amount: 9900, description: "Basic Audit" },
    extended: { amount: 24900, description: "Extended Report" }
  };

  const selected = products[product] || products.basic;

  try {
    const intent = await stripe.paymentIntents.create({
      amount: selected.amount,
      currency: "usd",
      receipt_email: email,
      description: selected.description,
      automatic_payment_methods: { enabled: true }
    });

    return res.json({ clientSecret: intent.client_secret });
  } catch (error) {
    return res.status(500).json({ message: "Failed to create payment intent." });
  }
});

app.listen(port, () => {
  console.log(`Aegis Sentinel server listening on port ${port}`);
});
