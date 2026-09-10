// Project data for the NAG demo portfolio dashboard.
// Each project is validated by script.js before rendering.
const projects = [
  {
    id: "p-aurora",
    name: "Aurora Web Platform",
    status: "in-progress",
    description:
      "Rebuilding the Aurora marketing site on a modern component system for faster load times and better conversions.",
    deadline: "2026-03-15",
    team: "Aurora Team"
  },
  {
    id: "p-pulse",
    name: "Pulse Analytics SDK",
    status: "completed",
    description:
      "Delivered a lightweight analytics SDK with event streaming, dashboards, and a public API for integration.",
    deadline: "2026-01-28",
    team: "Data Guild"
  },
  {
    id: "p-nimbus",
    name: "Nimbus Mobile App",
    status: "pending",
    description:
      "Native mobile experience for Nimbus customers, planned with offline-first sync and push notifications.",
    deadline: "2026-05-10",
    team: "Mobile Guild"
  },
  {
    id: "p-heron",
    name: "Heron Design System",
    status: "in-progress",
    description:
      "Standardizing tokens, components, and documentation to unify product teams around one accessible design language.",
    deadline: "2026-04-02",
    team: "Design Systems"
  },
  {
    id: "p-cobalt",
    name: "Cobalt Billing Migration",
    status: "on-hold",
    description:
      "Migrating legacy billing to a new subscription engine. Paused pending vendor contract review.",
    deadline: "2026-03-30",
    team: "Platform Squad"
  },
  {
    id: "p-solis",
    name: "Solis Customer Portal",
    status: "pending",
    description:
      "Self-service portal for order status, invoices, and support tickets to reduce call volume.",
    deadline: "2026-06-18",
    team: "Customer Experience"
  },
  {
    id: "p-marigold",
    name: "Marigold Release Notes",
    status: "completed",
    description:
      "Automated release note generation from commit history and issue tracking, published weekly to the docs site.",
    deadline: "2026-02-05",
    team: "Developer Advocacy"
  },
  {
    id: "p-fjord",
    name: "Fjord API v2",
    status: "in-progress",
    description:
      "Second-generation REST API with improved pagination, versioning, rate limits, and OpenAPI specifications.",
    deadline: "2026-05-01",
    team: "Platform Squad"
  },
  {
    id: "p-vetiver",
    name: "Vetiver Uptime Monitoring",
    status: "on-hold",
    description:
      "Global uptime and synthetic monitoring rollout. Blocked on regional infrastructure approvals.",
    deadline: "2026-02-20",
    team: "SRE"
  }
];

// Expose globally for script.js to consume.
if (typeof window !== "undefined") {
  window.projects = projects;
}