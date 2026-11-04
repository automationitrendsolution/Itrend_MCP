// iTrend MCP Platform — small page behaviours. Keep this file minimal; prefer HTMX.

document.addEventListener("DOMContentLoaded", () => {
  // Show Django messages rendered as toasts.
  document.querySelectorAll("#toastContainer .toast").forEach((el) => {
    bootstrap.Toast.getOrCreateInstance(el, { delay: 5000 }).show();
  });
});
