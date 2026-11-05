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

  // MCP form: show only the fields that apply to the chosen deployment type.
  // Elements use data-show-for="zip github" (space-separated types).
  const typeInputs = document.querySelectorAll('input[name="deployment_type"]');
  const toggleSections = () => {
    const checked = document.querySelector('input[name="deployment_type"]:checked');
    const type = checked ? checked.value : "";
    document.querySelectorAll("[data-show-for]").forEach((el) => {
      el.classList.toggle("d-none", !el.dataset.showFor.split(" ").includes(type));
    });
  };
  if (typeInputs.length) {
    typeInputs.forEach((input) => input.addEventListener("change", toggleSections));
    toggleSections();
  }
});
