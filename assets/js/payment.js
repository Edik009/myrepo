const paymentStatus = document.querySelector("[data-payment-status]");
const paymentSuccess = document.querySelector("[data-payment-success]");
const emailInput = document.querySelector("[data-payment-email]");
const currencySelect = document.querySelector("[data-payment-currency]");

const createPayment = async ({ product, amount, email, payCurrency }) => {
  const response = await fetch("/api/nowpayments/create-payment", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      product,
      amount,
      email,
      payCurrency
    })
  });

  if (!response.ok) {
    throw new Error("Payment request failed");
  }
  return response.json();
};

document.querySelectorAll("[data-payment-product]").forEach((button) => {
  button.addEventListener("click", async () => {
    if (!paymentStatus) {
      return;
    }
    const email = emailInput?.value;
    if (!email) {
      paymentStatus.textContent = "Please enter a valid email before checkout.";
      return;
    }
    const amount = Number(button.dataset.paymentAmount);
    const product = button.dataset.paymentProduct;
    const payCurrency = currencySelect?.value || "btc";

    paymentStatus.textContent = "Creating crypto payment...";
    paymentSuccess.style.display = "none";

    try {
      const result = await createPayment({ product, amount, email, payCurrency });
      paymentStatus.textContent = "Payment initialized. Redirecting to checkout...";
      if (result.paymentUrl) {
        window.open(result.paymentUrl, "_blank", "noopener");
      }
    } catch (error) {
      paymentStatus.textContent = "Unable to create payment. Please try again.";
    }
  });
});
