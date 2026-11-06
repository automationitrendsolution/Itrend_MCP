// iTrend MCP Platform — small page behaviours. Keep this file minimal; prefer HTMX.
//
//   Theme menu            data-pg-theme="light|dark|system" (stored in this browser)
//   Sidebar collapse      data-pg-sidebar-toggle, or the [ key
//   Global search         the / key focuses it
//   Copy buttons          data-copy-target="#inputId"
//   Confirmation dialog   <form data-confirm="Revoke this token?" data-confirm-action="Revoke">
//   Toasts                Django messages (partials/toasts.html)

(function () {
  "use strict";

  const root = document.documentElement;

  function store(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* private mode: the choice lasts for this page only */ }
  }

  // ---------------------------------------------------------------------
  // Theme: light / dark / system
  // ---------------------------------------------------------------------
  const darkQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function applyTheme(pref) {
    const dark = pref === "dark" || (pref === "system" && darkQuery && darkQuery.matches);
    root.setAttribute("data-bs-theme", dark ? "dark" : "light");
    root.setAttribute("data-theme-pref", pref);
    document.querySelectorAll("[data-pg-theme]").forEach((item) => {
      item.setAttribute("aria-checked", item.dataset.pgTheme === pref ? "true" : "false");
    });
  }

  if (darkQuery) {
    darkQuery.addEventListener("change", () => {
      if ((root.getAttribute("data-theme-pref") || "system") === "system") applyTheme("system");
    });
  }

  // ---------------------------------------------------------------------
  // Sidebar collapse (desktop)
  // ---------------------------------------------------------------------
  function setSidebar(collapsed) {
    if (collapsed) root.setAttribute("data-sidebar", "collapsed");
    else root.removeAttribute("data-sidebar");
    store("itmcp-sidebar", collapsed ? "collapsed" : "expanded");
    document.querySelectorAll("[data-pg-sidebar-toggle]").forEach((button) => {
      button.setAttribute("aria-pressed", collapsed ? "true" : "false");
      const label = collapsed ? "Expand navigation" : "Collapse navigation";
      button.setAttribute("aria-label", label);
      button.title = `${label} ( [ )`;
    });
  }
  const sidebarCollapsed = () => root.getAttribute("data-sidebar") === "collapsed";

  // ---------------------------------------------------------------------
  // Confirmation dialog (accessible replacement for window.confirm)
  // ---------------------------------------------------------------------
  let dialog = null;

  function buildDialog() {
    const el = document.createElement("div");
    el.className = "modal fade pg-confirm";
    el.tabIndex = -1;
    el.setAttribute("aria-labelledby", "pgConfirmTitle");
    el.setAttribute("aria-describedby", "pgConfirmMessage");
    el.innerHTML = `
      <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content">
          <div class="modal-body">
            <span class="pg-confirm-icon" aria-hidden="true"><i class="bi bi-exclamation-triangle"></i></span>
            <div>
              <h2 class="modal-title mb-1" id="pgConfirmTitle">Please confirm</h2>
              <p class="mb-0 text-body-secondary" id="pgConfirmMessage"></p>
            </div>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancel</button>
            <button type="button" class="btn btn-danger pg-btn-solid" data-pg-confirm-ok>Confirm</button>
          </div>
        </div>
      </div>`;
    document.body.appendChild(el);
    return el;
  }

  function askToConfirm(message, actionLabel) {
    dialog = dialog || buildDialog();
    dialog.querySelector("#pgConfirmMessage").textContent = message;
    const ok = dialog.querySelector("[data-pg-confirm-ok]");
    ok.textContent = actionLabel || "Confirm";
    const modal = bootstrap.Modal.getOrCreateInstance(dialog);
    return new Promise((resolve) => {
      let confirmed = false;
      const onOk = () => { confirmed = true; modal.hide(); };
      ok.addEventListener("click", onOk, { once: true });
      dialog.addEventListener("shown.bs.modal", () => dialog.querySelector("[data-bs-dismiss]").focus(), { once: true });
      dialog.addEventListener("hidden.bs.modal", () => { ok.removeEventListener("click", onOk); resolve(confirmed); }, { once: true });
      modal.show();
    });
  }

  // Listening on the document also covers forms that HTMX adds to the page later.
  document.addEventListener("submit", (event) => {
    const form = event.target;
    const message = form.dataset ? form.dataset.confirm : null;
    if (!message) return;
    if (form.dataset.pgConfirmed === "1") { delete form.dataset.pgConfirmed; return; }
    event.preventDefault();
    const submitter = event.submitter || null;
    if (typeof bootstrap === "undefined") {  // scripts not loaded yet: fall back to the browser dialog
      if (window.confirm(message)) { form.dataset.pgConfirmed = "1"; submitter ? form.requestSubmit(submitter) : form.requestSubmit(); }
      return;
    }
    askToConfirm(message, form.dataset.confirmAction).then((yes) => {
      if (yes) {
        form.dataset.pgConfirmed = "1";
        if (form.requestSubmit) { submitter ? form.requestSubmit(submitter) : form.requestSubmit(); } else { form.submit(); }
      } else if (submitter) {
        submitter.focus();
      }
    });
  });

  // ---------------------------------------------------------------------
  // Keyboard shortcuts: "/" search, "[" sidebar. Ignored while typing.
  // ---------------------------------------------------------------------
  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const target = event.target;
    const typing = target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
    if (typing) {
      if (event.key === "Escape" && target.id === "globalSearch") target.blur();
      return;
    }
    if (event.key === "/") {
      const search = document.getElementById("globalSearch");
      if (search && search.offsetParent !== null) { event.preventDefault(); search.focus(); }
    } else if (event.key === "[" && window.matchMedia("(min-width: 992px)").matches) {
      event.preventDefault();
      setSidebar(!sidebarCollapsed());
    }
  });

  // ---------------------------------------------------------------------
  // On load
  // ---------------------------------------------------------------------
  document.addEventListener("DOMContentLoaded", () => {
    applyTheme(root.getAttribute("data-theme-pref") || "system");
    document.querySelectorAll("[data-pg-theme]").forEach((item) => {
      item.addEventListener("click", () => {
        store("itmcp-theme", item.dataset.pgTheme);
        applyTheme(item.dataset.pgTheme);
      });
    });

    setSidebar(sidebarCollapsed());
    document.querySelectorAll("[data-pg-sidebar-toggle]").forEach((button) => {
      button.addEventListener("click", () => setSidebar(!sidebarCollapsed()));
    });

    // Django messages as toasts.
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
          button.innerHTML = '<i class="bi bi-check2 me-1" aria-hidden="true"></i>Copied';
        } catch {
          source.select();
          button.textContent = "Press Ctrl+C";
        }
        setTimeout(() => { button.innerHTML = original; }, 2000);
      });
    });
  });
})();
