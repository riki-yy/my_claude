// Recovery habit analytics app logic.
(function () {
  "use strict";

  const data = window.HABIT_DATA || [];
  const grid = document.getElementById("habit-grid");
  const filters = document.getElementById("filters");
  const statsEls = {
    completed: document.getElementById("stat-completed"),
    total: document.getElementById("stat-total"),
    streak: document.getElementById("stat-streak"),
  };

  let activeFilter = "all";
  const svgCheck =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6L9 17l-5-5"/></svg>';

  // Build category filter chips
  function buildFilters() {
    const categories = [...new Set(data.map((h) => h.category))].sort();
    categories.forEach((cat) => {
      const btn = document.createElement("button");
      btn.className = "filter-chip";
      btn.dataset.filter = cat;
      btn.textContent = cat;
      filters.appendChild(btn);
    });

    filters.addEventListener("click", (e) => {
      const chip = e.target.closest(".filter-chip");
      if (!chip) return;
      activeFilter = chip.dataset.filter;
      document
        .querySelectorAll(".filter-chip")
        .forEach((c) => c.classList.toggle("is-active", c === chip));
      render();
    });
  }

  // Create a single habit card element
  function createCard(habit) {
    const card = document.createElement("article");
    card.className = "habit-card";
    card.dataset.habitId = habit.id;
    card.setAttribute("role", "button");
    card.setAttribute("tabindex", "0");
    card.setAttribute(
      "aria-pressed",
      habit.completedToday ? "true" : "false"
    );
    card.style.setProperty("--cat-color", habit.color);

    if (habit.completedToday) card.classList.add("is-completed");

    const history = habit.history || [];
    const completedCount = history.filter(Boolean).length;
    const totalCount = history.length;
    const percent = totalCount ? Math.round((completedCount / totalCount) * 100) : 0;

    card.innerHTML =
      '<div class="card-top">' +
      '<span class="card-category">' + escapeHtml(habit.category) + "</span>" +
      '<span class="check">' + svgCheck + "</span>" +
      "</div>" +
      '<h2 class="card-title">' + escapeHtml(habit.title) + "</h2>" +
      '<p class="card-desc">' + escapeHtml(habit.description || "") + "</p>" +
      '<div class="card-meta">' +
      '<div class="card-progress" title="' + completedCount + " of " + totalCount + " days completed\">" +
      '<span class="progress-bar"><span class="progress-fill" style="width:' + percent + '%"></span></span>' +
      '<span class="progress-label">' + percent + "%</span>" +
      "</div>" +
      '<span class="card-streak">' +
      '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13 2L3 14h7l-1 8 10-12h-7l1-8z"/></svg>' +
      (habit.currentStreak || 0) +
      " day streak</span>" +
      "</div>";

    card.addEventListener("click", () => toggleHabit(habit, card));
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        toggleHabit(habit, card);
      }
    });

    return card;
  }

  // Toggle a habit's completion for today
  function toggleHabit(habit, card) {
    habit.completedToday = !habit.completedToday;
    habit.currentStreak = habit.completedToday
      ? (habit.currentStreak || 0) + 1
      : Math.max(0, (habit.currentStreak || 0) - 1);

    card.classList.toggle("is-completed", habit.completedToday);
    card.setAttribute("aria-pressed", habit.completedToday ? "true" : "false");
    card.querySelector(".card-streak").innerHTML =
      '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13 2L3 14h7l-1 8 10-12h-7l1-8z"/></svg>' +
      habit.currentStreak +
      " day streak";

    updateStats();
  }

  // Re-render the card grid based on the active filter
  function render() {
    grid.innerHTML = "";
    const visible = data.filter(
      (h) => activeFilter === "all" || h.category === activeFilter
    );
    visible.forEach((h) => grid.appendChild(createCard(h)));
  }

  // Update the header stats strip
  function updateStats() {
    const completed = data.filter((h) => h.completedToday).length;
    const total = data.length;
    const bestStreak = data.reduce(
      (max, h) => Math.max(max, h.bestStreak || 0),
      0
    );

    statsEls.completed.textContent = completed;
    statsEls.total.textContent = total;
    statsEls.streak.textContent = bestStreak;
  }

  // Basic escaping for safe insertion into HTML
  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Init
  buildFilters();
  render();
  updateStats();
})();