const express = require("express");
const nodemailer = require("nodemailer");
const cors = require("cors");
const Stripe = require("stripe");
require("dotenv").config();

const app = express();
const port = process.env.PORT || 3000;

const stripeSecret = process.env.STRIPE_SECRET_KEY;
const stripe = stripeSecret ? new Stripe(stripeSecret) : null;

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

app.post("/api/contact", async (req, res) => {
  const { name, email, message, service, type } = req.body;

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
      text: `Name: ${name}\nEmail: ${email}\nService: ${service || "N/A"}\nType: ${type || "N/A"}\nMessage: ${message || ""}`
    });

    return res.json({ message: "Contact request sent." });
  } catch (error) {
    return res.status(500).json({ message: "Failed to send email." });
  }
});

app.post("/api/telegram", async (req, res) => {
  const { name, email, message, service, type } = req.body;
  const { BOT_TOKEN, CHAT_ID } = process.env;

  if (!BOT_TOKEN || !CHAT_ID) {
    return res.status(500).json({ message: "Telegram config missing." });
  }

  if (!name || !email || !isValidEmail(email)) {
    return res.status(400).json({ message: "Invalid contact data." });
  }

  const text = [
    "<b>New Aegis Sentinel request</b>",
    `<b>Type:</b> ${type || "contact"}`,
    `<b>Name:</b> ${name}`,
    `<b>Email:</b> ${email}`,
    `<b>Service:</b> ${service || "N/A"}`,
    `<b>Message:</b> ${message || ""}`
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
