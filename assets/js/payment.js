const paymentForm = document.querySelector("[data-payment-form]");
const paymentStatus = document.querySelector("[data-payment-status]");
const paymentSuccess = document.querySelector("[data-payment-success]");

if (paymentForm) {
  paymentForm.addEventListener("submit", (event) => {
    event.preventDefault();
    paymentStatus.textContent = "Payment processing is currently in demo mode.";
    paymentSuccess.style.display = "block";
    paymentForm.reset();
  });
}
