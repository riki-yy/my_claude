(function () {
  "use strict";

  const STATUSES = ["in-progress", "pending", "completed", "on-hold"];
  const STATUS_LABELS = {
    "in-progress": "In Progress",
    pending: "Pending",
    completed: "Completed",
    "on-hold": "On Hold"
  };

  const app = document.getElementById("app");
  const summaryEl = document.getElementById("summary");
  const cardsGridEl = document.getElementById("cards");
  const searchInput = document.getElementById("search");
  const filterChips = Array.from(document.querySelectorAll(".filter-chip"));

  let activeStatus = "all";
  let searchTerm = "";

  function normalize(value) {
    return String(value || "").trim().toLowerCase();
  }

  function validateProjects(list) {
    if (!Array.isArray(list)) {
      throw new Error("projects must be an array");
    }
    if (list.length === 0) {
      return [];
    }
    return list.map(function (item, index) {
      if (!item || typeof item !== "object" || Array.isArray(item)) {
        throw new Error("Project at index " + index + " must be an object");
      }
      if (!item.id || typeof item.id !== "string") {
        throw new Error("Project '" + (item.name || index) + "' is missing a string 'id'");
      }
      if (!item.name || typeof item.name !== "string") {
        throw new Error("Project '" + item.id + "' is missing a string 'name'");
      }
      if (!STATUSES.includes(item.status)) {
        throw new Error(
          "Project '" + item.id + "' has invalid status: " + item.status
        );
      }
      if (typeof item.description !== "string") {
        throw new Error("Project '" + item.id + "' is missing a description");
      }
      if (item.deadline && isNaN(Date.parse(item.deadline))) {
        throw new Error("Project '" + item.id + "' has an invalid deadline: " + item.deadline);
      }
      return item;
    });
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatDate(value) {
    if (!value) return "No deadline";
    const date = new Date(value + "T00:00:00");
    return date.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric"
    });
  }

  function isOverdue(item) {
    if (!item.deadline) return false;
    const deadline = new Date(item.deadline + "T00:00:00");
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    return deadline < today && item.status !== "completed";
  }

  function filteredProjects(list) {
    return list.filter(function (item) {
      const matchesStatus =
        activeStatus === "all" || item.status === activeStatus;
      if (!matchesStatus) return false;
      if (!searchTerm) return true;
      const haystack = [item.name, item.description, item.team, item.id]
        .join(" ")
        .toLowerCase();
      return haystack.includes(searchTerm);
    });
  }

  function renderSummary(list) {
    const counts = {
      total: list.length,
      "in-progress": 0,
      pending: 0,
      completed: 0,
      "on-hold": 0
    };
    list.forEach(function (item) {
      if (STATUSES.includes(item.status)) {
        counts[item.status] += 1;
      }
    });

    let html = "";
    html += summaryCardHtml("total", "Total Projects", counts.total);
    STATUSES.forEach(function (status) {
      html += summaryCardHtml(status, STATUS_LABELS[status], counts[status]);
    });
    summaryEl.innerHTML = html;
  }

  function summaryCardHtml(status, label, count) {
    return (
      '<div class="summary-card" data-status="' +
      status +
      '">' +
      '<div class="count">' +
      count +
      "</div>" +
      '<div class="label">' +
      label +
      "</div>" +
      "</div>"
    );
  }

  function projectCardHtml(item) {
    const deadlineClass = isOverdue(item) ? "deadline overdue" : "deadline";
    const deadlineText = isOverdue(item) ? "Overdue · " : "";
    return (
      '<article class="project-card">' +
      '<div class="card-header">' +
      "<h3>" +
      escapeHtml(item.name) +
      "</h3>" +
      '<span class="status-badge status-' +
      item.status +
      '">' +
      escapeHtml(STATUS_LABELS[item.status]) +
      "</span>" +
      "</div>" +
      '<p class="description">' +
      escapeHtml(item.description) +
      "</p>" +
      '<div class="card-footer">' +
      '<span class="' +
      deadlineClass +
      '">' +
      deadlineText +
      formatDate(item.deadline) +
      "</span>" +
      "<span>" +
      escapeHtml(item.team || "") +
      "</span>" +
      "</div>" +
      "</article>"
    );
  }

  function renderCards(list) {
    if (!list.length) {
      cardsGridEl.innerHTML =
        '<p class="empty-state">No projects match the current filters.</p>';
      return;
    }
    cardsGridEl.innerHTML = list.map(projectCardHtml).join("");
  }

  function render(projects) {
    const visible = filteredProjects(projects);
    renderSummary(projects);
    renderCards(visible);
  }

  function init() {
    let projects;
    try {
      projects = validateProjects(window.projects);
    } catch (err) {
      cardsGridEl.innerHTML =
        '<p class="empty-state">Could not load projects: ' +
        escapeHtml(err.message) +
        "</p>";
      console.error(err);
      return;
    }

    if (projects.length === 0) {
      summaryEl.innerHTML =
        '<div class="summary-card total" data-status="total">' +
        '<div class="count">0</div>' +
        '<div class="label">Total Projects</div>' +
        "</div>";
      cardsGridEl.innerHTML =
        '<p class="empty-state">No projects available yet.</p>';
      filterChips.forEach(function (chip) {
        chip.disabled = true;
      });
      searchInput.disabled = true;
      return;
    }

    // Filter chips
    filterChips.forEach(function (chip) {
      chip.addEventListener("click", function () {
        activeStatus = chip.dataset.status;
        filterChips.forEach(function (c) {
          c.classList.toggle("is-active", c === chip);
        });
        render(projects);
      });
    });

    // Search with debounce
    searchInput.addEventListener("input", function () {
      clearTimeout(searchInput._timer);
      searchInput._timer = setTimeout(function () {
        searchTerm = normalize(searchInput.value);
        render(projects);
      }, 120);
    });

    render(projects);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();