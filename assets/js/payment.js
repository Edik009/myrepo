const paymentForm = document.querySelector("[data-payment-form]");
const paymentStatus = document.querySelector("[data-payment-status]");
const paymentSuccess = document.querySelector("[data-payment-success]");

if (paymentForm) {
  // TODO: Replace with your Stripe publishable key.
  const stripe = Stripe("YOUR_PUBLISHABLE_KEY");
  const elements = stripe.elements();
  const card = elements.create("card", {
    style: {
      base: {
        color: "#0b1d2f",
        fontSize: "16px",
        fontFamily: "Inter, Open Sans, sans-serif",
      },
    },
  });

  card.mount("#card-element");

  paymentForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    paymentStatus.textContent = "";

    const formData = new FormData(paymentForm);
    const email = formData.get("email");
    const product = formData.get("product");

    const intentResponse = await fetch("/api/create-payment-intent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, product }),
    });

    const intentData = await intentResponse.json();

    if (!intentResponse.ok) {
      paymentStatus.textContent = intentData.message || "Payment error.";
      return;
    }

    const { error, paymentIntent } = await stripe.confirmCardPayment(
      intentData.clientSecret,
      {
        payment_method: {
          card,
          billing_details: { email },
        },
      }
    );

    if (error) {
      paymentStatus.textContent = error.message;
      return;
    }

    if (paymentIntent.status === "succeeded") {
      paymentSuccess.style.display = "block";
      paymentStatus.textContent = "";
      paymentForm.reset();
    }
  });
}
