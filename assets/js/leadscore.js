const userActions = {
  viewedPricing: false,
  timeOnSite: 0,
};

const trackPageView = (page) => {
  if (page === "payment") {
    userActions.viewedPricing = true;
  }
};

const trackTime = () => {
  setInterval(() => {
    userActions.timeOnSite += 10;
  }, 10000);
};

const calculateScore = () => {
  let score = 0;
  if (userActions.viewedPricing) {
    score += 50;
  }
  score += Math.min(30, Math.floor(userActions.timeOnSite / 10));

  const lastVisit = localStorage.getItem("lastVisit");
  const now = Date.now();
  if (lastVisit && now - Number(lastVisit) <= 24 * 60 * 60 * 1000) {
    score += 20;
  }
  localStorage.setItem("lastVisit", String(now));

  return score;
};

trackTime();
