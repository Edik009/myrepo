const paymentStatus = document.querySelector("[data-payment-status]");
const emailInput = document.querySelector("[data-payment-email]");
const currencySelect = document.querySelector("[data-payment-currency]");
const modal = document.querySelector("[data-payment-modal]");
const modalCloseButtons = document.querySelectorAll("[data-payment-close]");
const modalConfirm = document.querySelector("[data-payment-confirm]");
const tierButtons = document.querySelectorAll("[data-payment-tier]");
const planCards = document.querySelectorAll("[data-payment-card]");
let pendingPayment = null;

const resolveApiBase = () => {
  if (window.location.port === "3000") {
    return "";
  }
  return "http://localhost:3000";
};

const createPayment = async ({ product, amount, email, payCurrency }) => {
  const response = await fetch(`${resolveApiBase()}/api/nowpayments/create-payment`, {
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
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.message || "Payment request failed");
  }
  return response.json();
};

const openModal = () => {
  if (!modal) return;
  modal.classList.add("open");
  document.body.style.overflow = "hidden";
};

const closeModal = () => {
  if (!modal) return;
  modal.classList.remove("open");
  document.body.style.overflow = "";
};

modalCloseButtons.forEach((button) => {
  button.addEventListener("click", closeModal);
});

tierButtons.forEach((button) => {
  button.addEventListener("click", () => {
    tierButtons.forEach((btn) => btn.classList.toggle("active", btn === button));
    const tier = button.dataset.paymentTier;
    planCards.forEach((card) => {
      card.style.display = card.dataset.paymentCard === tier ? "grid" : "none";
    });
  });
});

document.querySelectorAll("[data-payment-product]").forEach((button) => {
  button.addEventListener("click", () => {
    pendingPayment = {
      amount: Number(button.dataset.paymentAmount),
      product: button.dataset.paymentProduct
    };
    openModal();
  });
});

if (modalConfirm) {
  modalConfirm.addEventListener("click", async () => {
    if (!pendingPayment || !paymentStatus) {
      return;
    }
    const email = emailInput?.value;
    if (!email) {
      paymentStatus.textContent = "Please enter a valid email before checkout.";
      return;
    }
    const payCurrency = currencySelect?.value || "btc";
    paymentStatus.textContent = "Creating crypto payment...";

    try {
      const result = await createPayment({
        product: pendingPayment.product,
        amount: pendingPayment.amount,
        email,
        payCurrency
      });
      paymentStatus.textContent = "Payment initialized. Redirecting to checkout...";
      closeModal();
      if (result.paymentUrl) {
        window.open(result.paymentUrl, "_blank", "noopener");
      }
    } catch (error) {
      paymentStatus.textContent = error.message || "Unable to create payment. Please try again.";
    }
  });
}
