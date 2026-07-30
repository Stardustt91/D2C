// Shell controller: theme switching, the mobile nav drawer, and the floating
// AI Assistant window. Kept separate from the estimation logic in script.js.
(function () {
  'use strict';

  var root = document.documentElement;
  var shell = document.querySelector('.app-shell');

  /* ---------------- Theme ---------------- */
  function applyTheme(theme) {
    root.setAttribute('data-theme', theme);
    root.setAttribute('data-bs-theme', theme);
    try { localStorage.setItem('d2c-theme', theme); } catch (e) {}
  }
  function toggleTheme() {
    applyTheme(root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
  }
  ['theme-toggle', 'theme-toggle-mini'].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener('click', toggleTheme);
  });

  /* ---------------- Mobile drawer ---------------- */
  function closeDrawer() { if (shell) shell.classList.remove('nav-open'); }
  function openDrawer() { if (shell) shell.classList.add('nav-open'); }

  var menuBtn = document.getElementById('menu-btn');
  if (menuBtn) menuBtn.addEventListener('click', function () {
    shell.classList.contains('nav-open') ? closeDrawer() : openDrawer();
  });
  var sidebarScrim = document.getElementById('sidebar-scrim');
  if (sidebarScrim) sidebarScrim.addEventListener('click', closeDrawer);

  /* ---------------- Floating AI Assistant window ---------------- */
  var assistantWindow = document.getElementById('assistant-window');
  var assistantScrim = document.getElementById('assistant-scrim');
  var assistantClose = document.getElementById('assistant-close');
  var chatBooted = false;

  function openAssistant() {
    if (!assistantWindow) return;
    assistantWindow.hidden = false;
    // Force reflow so the entrance transition runs.
    void assistantWindow.offsetWidth;
    shell.classList.add('assistant-open');
    if (!chatBooted && typeof window.initChatAssistant === 'function') {
      chatBooted = true;
      window.initChatAssistant();
    }
    var input = document.getElementById('user-input');
    if (input && !input.disabled) setTimeout(function () { input.focus(); }, 150);
  }
  function closeAssistant() {
    if (!assistantWindow) return;
    shell.classList.remove('assistant-open');
    assistantWindow.hidden = true;
  }
  window.openAssistant = openAssistant;
  window.closeAssistant = closeAssistant;

  if (assistantClose) assistantClose.addEventListener('click', closeAssistant);
  if (assistantScrim) assistantScrim.addEventListener('click', closeAssistant);

  var btnOpenAssistant = document.getElementById('btn-open-assistant');
  if (btnOpenAssistant) btnOpenAssistant.addEventListener('click', openAssistant);

  /* ---------------- Sidebar nav ---------------- */
  document.querySelectorAll('.nav-item[data-tab]').forEach(function (item) {
    item.addEventListener('click', function () {
      var tab = item.getAttribute('data-tab');
      closeDrawer();
      if (tab === 'assistant') {
        openAssistant();
      } else {
        closeAssistant();
        window.scrollTo({ top: 0, behavior: 'smooth' });
      }
    });
  });

  /* ---------------- Global keys ---------------- */
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    if (shell && shell.classList.contains('assistant-open')) closeAssistant();
    closeDrawer();
  });
})();
