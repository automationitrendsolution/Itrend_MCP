// iTrend MCP Platform — small page behaviours. Keep this file minimal; prefer HTMX.

document.addEventListener("DOMContentLoaded", () => {
  // Show Django messages rendered as toasts.
  document.querySelectorAll("#toastContainer .toast").forEach((el) => {
    bootstrap.Toast.getOrCreateInstance(el, { delay: 5000 }).show();
  });

  // Copy buttons: <button data-copy-target="#inputId">.
  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", async () => {
      const source = document.querySelector(button.dataset.copyTarget);
      const original = button.innerHTML;
      try {
        await navigator.clipboard.writeText(source.value);
        button.innerHTML = '<i class="bi bi-check2 me-1"></i>Copied';
      } catch {
        source.select();
        button.innerHTML = "Press Ctrl+C";
      }
      setTimeout(() => { button.innerHTML = original; }, 2000);
    });
  });
});

// Confirmation dialogs: <form data-confirm="Revoke this token?">. Listening on the
// document also covers forms that HTMX adds to the page later.
document.addEventListener("submit", (event) => {
  const message = event.target.dataset ? event.target.dataset.confirm : null;
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});
