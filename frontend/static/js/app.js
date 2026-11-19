// iTrend MCP Platform — small page behaviours. Keep this file minimal; prefer HTMX.
//
//   Theme menu            data-pg-theme="light|dark|system" (stored in this browser)
//   Sidebar collapse      data-pg-sidebar-toggle, or the [ key
//   Global search         the / key focuses it
//   Copy buttons          data-copy-target="#inputId"
//   Confirmation dialog   <form data-confirm="Revoke this token?" data-confirm-action="Revoke">
//   Toasts                Django messages (partials/toasts.html)
//   Background forms      POST forms on app pages submit without reloading: a spinner shows in the
//                         clicked button, not in the browser tab. Opt out with data-no-boost.

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
    if (!dialog || !dialog.isConnected) dialog = buildDialog();  // the page body may have been replaced
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
    if (isBoosted(form)) return;  // background forms confirm in "htmx:confirm" below
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
  // Background forms (HTMX boost on POST forms inside the app)
  // ---------------------------------------------------------------------
  const BOOST_SKIP = "[data-no-boost], [hx-post], [hx-get], [hx-put], [hx-patch], [hx-delete], [target]";
  const isBoosted = (el) => el instanceof HTMLFormElement && el.getAttribute("hx-boost") === "true";
  // The form that sent a request (swap events are fired on the page body, not on the form).
  const sourceOf = (event) => (event.detail.requestConfig && event.detail.requestConfig.elt) || event.detail.elt;

  function boostForms(scope) {
    if (typeof htmx === "undefined" || !scope.querySelectorAll) return;
    const forms = scope.matches && scope.matches("form") ? [scope] : scope.querySelectorAll("form");
    forms.forEach((form) => {
      if ((form.getAttribute("method") || "").toLowerCase() !== "post") return;
      if (!form.closest(".app-shell") || form.matches(BOOST_SKIP) || form.hasAttribute("hx-boost")) return;
      form.setAttribute("hx-boost", "true");
      htmx.process(form);
    });
  }

  function setBusy(button, busy) {
    if (!button) return;
    if (busy) {
      button.dataset.pgLabel = button.innerHTML;
      button.style.minWidth = `${button.offsetWidth}px`;  // keep the button's width while it spins
      button.innerHTML = '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span>'
        + '<span class="visually-hidden">Working…</span>';
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    } else if (button.dataset.pgLabel !== undefined) {
      button.innerHTML = button.dataset.pgLabel;
      delete button.dataset.pgLabel;
      button.style.minWidth = "";
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }

  // Ask first for forms with data-confirm, then send.
  document.addEventListener("htmx:confirm", (event) => {
    const form = event.detail.elt;
    if (!isBoosted(form) || !form.dataset.confirm) return;
    event.preventDefault();
    askToConfirm(form.dataset.confirm, form.dataset.confirmAction).then((yes) => {
      if (yes) event.detail.issueRequest(true);
    });
  });

  document.addEventListener("htmx:beforeRequest", (event) => {
    const form = event.detail.elt;
    if (!isBoosted(form)) return;
    const trigger = event.detail.requestConfig && event.detail.requestConfig.triggeringEvent;
    const button = (trigger && trigger.submitter) || form.querySelector('[type="submit"], button:not([type])');
    form.pgBusyButton = button;
    setBusy(button, true);
  });

  document.addEventListener("htmx:afterRequest", (event) => {
    const form = sourceOf(event);
    if (isBoosted(form) && !event.detail.successful) setBusy(form.pgBusyButton, false);
  });

  document.addEventListener("htmx:beforeSwap", (event) => {
    if (!isBoosted(sourceOf(event))) return;
    const xhr = event.detail.xhr;
    const html = xhr.responseText || "";
    if (xhr.status < 400 && !/<body[^>]*class="[^"]*app-body/.test(html)) {
      // Not an app page (e.g. signed out -> login page): load it normally.
      event.detail.shouldSwap = false;
      window.location.assign(xhr.responseURL || window.location.href);
      return;
    }
    // Error pages (403, 500 …) are shown too, instead of nothing happening.
    event.detail.shouldSwap = true;
    event.detail.isError = false;
  });

  document.addEventListener("htmx:afterSettle", (event) => {
    if (!isBoosted(sourceOf(event))) return;
    const url = event.detail.xhr && event.detail.xhr.responseURL;
    if (url) {
      const next = new URL(url);
      if (next.origin === location.origin && next.pathname + next.search !== location.pathname + location.search) {
        history.pushState({ htmx: true }, "", next.pathname + next.search);
      }
    }
    initPage(true);
  });

  // Back/forward restores a saved copy of the page: wire it up again, without replaying old toasts.
  document.addEventListener("htmx:historyRestore", () => initPage(false));

  // ---------------------------------------------------------------------
  // Page set-up: on load, and again after a background form replaced the page
  // ---------------------------------------------------------------------
  function initPage(showToasts) {
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

    // Collapsed sidebar: a click on its empty space (not a link or button) opens it.
    const sidebar = document.getElementById("appSidebar");
    if (sidebar) {
      sidebar.addEventListener("click", (event) => {
        if (!sidebarCollapsed() || !window.matchMedia("(min-width: 992px)").matches) return;
        if (event.target.closest("a, button, input, select, textarea, label")) return;
        setSidebar(false);
      });
    }

    // Django messages as toasts.
    document.querySelectorAll("#toastContainer .toast").forEach((el) => {
      if (showToasts) bootstrap.Toast.getOrCreateInstance(el, { delay: 5000 }).show();
      else el.remove();
    });

    // Copy buttons: <button data-copy-target="#inputId">.
    document.querySelectorAll("[data-copy-target]").forEach((button) => {
      button.addEventListener("click", async () => {
        const source = document.querySelector(button.dataset.copyTarget);
        const original = button.innerHTML;
        try {
          await navigator.clipboard.writeText("value" in source ? source.value : source.textContent);
          button.innerHTML = '<i class="bi bi-check2 me-1" aria-hidden="true"></i>Copied';
        } catch {
          if (source.select) source.select();
          button.textContent = "Press Ctrl+C";
        }
        setTimeout(() => { button.innerHTML = original; }, 2000);
      });
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    initPage(true);
    // Forms already on the page, and forms that later HTMX swaps bring in (e.g. Start / Stop).
    if (typeof htmx !== "undefined") htmx.onLoad((element) => boostForms(element));
  });
})();


// ---------------------------------------------------------------------------
// Switchboard: the Pulse Line and clickable table rows.
// ---------------------------------------------------------------------------
(function () {
  "use strict";

  // Clickable rows: <tr data-href="...">. The row's own link stays the keyboard target.
  document.addEventListener("click", (event) => {
    const row = event.target.closest("tr[data-href]");
    if (!row || event.target.closest("a, button, input, select, textarea, label")) return;
    if (window.getSelection && String(window.getSelection())) return; // selecting text
    if (event.ctrlKey || event.metaKey) window.open(row.dataset.href, "_blank", "noopener");
    else window.location.href = row.dataset.href;
  });

  // The Pulse Line: one dot per real request through the gateway (amber for refusals).
  // Reads two numbers every few seconds; sends at most three dots of each kind per reading.
  const findLine = () => document.querySelector(".pg-pulse-line[data-pulse-url]");
  if (!findLine()) return;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let last = null;

  function send(kind, delay) {
    if (reduce) return;
    const dot = document.createElement("span");
    dot.className = "pg-pulse-dot" + (kind === "amber" ? " pg-pulse-dot--amber" : "");
    dot.style.animationDelay = delay + "ms";
    dot.addEventListener("animationend", () => dot.remove());
    const line = findLine();
    if (line) line.appendChild(dot);
  }

  async function poll() {
    if (document.hidden) return;
    try {
      const line = findLine();
      if (!line) return;
      const response = await fetch(line.dataset.pulseUrl, { credentials: "same-origin", headers: { Accept: "application/json" } });
      if (!response.ok) return;
      const now = await response.json();
      if (last) {
        const answered = Math.min(Math.max(now.answered - last.answered, 0), 3);
        const refused = Math.min(Math.max(now.refused - last.refused, 0), 3);
        for (let i = 0; i < answered; i++) send("signal", i * 220);
        for (let i = 0; i < refused; i++) send("amber", (answered + i) * 220);
      }
      last = now;
    } catch (e) { /* offline for a moment: try again next time */ }
  }
  poll();
  window.setInterval(poll, 5000);
})();


// ---------------------------------------------------------------------------
// Segmented control: <button data-sb-seg="panelId"> shows that panel, hides its siblings.
// ---------------------------------------------------------------------------
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-sb-seg]");
  if (!button) return;
  button.parentElement.querySelectorAll("[data-sb-seg]").forEach((other) => {
    const on = other === button;
    other.classList.toggle("is-active", on);
    other.setAttribute("aria-selected", on ? "true" : "false");
    const panel = document.getElementById(other.dataset.sbSeg);
    if (panel) panel.hidden = !on;
  });
});
