// Estimation sessions: the sidebar list, and the autosave that keeps it current.
//
// A session is created when the analyst starts an estimation, titled by the model,
// and then updated in place as the workflow progresses — so the cost hierarchy
// survives closing the tab, which it previously did not.
//
// script.js owns the live estimation state; this file owns persistence and the list.
// They meet at four documented hooks and nowhere else:
//   D2CSessions.beginSession()   script.js -> here, when an estimation starts
//   D2CSessions.noteChange()     script.js -> here, whenever the structure changes
//   window.loadSessionState()    here -> script.js, to reopen a saved session
//   window.getEstimationState()  here -> script.js, to read what should be saved
(function () {
  'use strict';

  var listEl = document.getElementById('session-list');
  var emptyEl = document.getElementById('sessions-empty');
  var btnNew = document.getElementById('btn-new-session');
  if (!listEl) return;

  // Long enough that a burst of edits collapses into one write, short enough that
  // the sidebar's "Edited" stamp is never visibly stale.
  var SAVE_DEBOUNCE_MS = 1200;

  var sessions = [];
  var activeId = null;
  /** In-flight POST /api/sessions. A save that lands before it resolves waits on it. */
  var pendingCreate = null;
  var saveTimer = null;
  var dirty = false;
  /** True while we are driving script.js ourselves, so its redraws are not read
   *  back as analyst edits — opening a session would otherwise "modify" it. */
  var suppressChanges = false;

  var renameModal = null;
  var deleteModal = null;
  var pendingActionId = null;

  /* ------------------------------------------------------------------ API */

  function api(method, url, body, keepalive) {
    var opts = { method: method, headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined && body !== null) opts.body = JSON.stringify(body);
    if (keepalive) opts.keepalive = true;
    return fetch(url, opts).then(function (res) {
      if (!res.ok) {
        return res.text().then(function (text) {
          var msg = 'Request failed (' + res.status + ')';
          try { var j = JSON.parse(text); if (j.detail) msg = j.detail; } catch (e) {}
          throw new Error(msg);
        });
      }
      return res.status === 204 ? null : res.json();
    });
  }

  function reportError(action, err) {
    console.warn('[sessions] ' + action + ' failed:', err);
    if (typeof window.setStatus === 'function') {
      window.setStatus('Could not ' + action + ': ' + (err.message || 'request failed'), true);
    }
  }

  /**
   * Put the failure where the missing sessions would have been.
   *
   * These requests fail in the one place nothing is watching: an empty sidebar looks
   * identical to "you have no sessions yet", and the estimation status line that
   * reportError writes to is overwritten by the next progress message. A stale build
   * or an un-restarted server therefore presented as the feature silently not
   * existing, so the reason is now shown in the list itself.
   */
  function showListError(action, err) {
    reportError(action, err);
    if (!emptyEl) return;
    emptyEl.hidden = false;
    emptyEl.classList.add('sessions-error');
    var detail = err && err.message ? err.message : 'request failed';
    emptyEl.textContent = 'Could not ' + action + ' — ' + detail +
      '. If this says 404, the running server predates the sessions feature: restart it.';
  }

  function clearListError() {
    if (!emptyEl) return;
    emptyEl.classList.remove('sessions-error');
    emptyEl.textContent = 'No sessions yet. Describe an activity and start an estimation.';
  }

  /* ------------------------------------------------------- Date formatting */

  // Stored stamps are ISO-8601 UTC ("...Z"), so the browser renders them in the
  // analyst's own timezone.
  function parseTs(value) {
    if (!value) return null;
    var d = new Date(value);
    return isNaN(d.getTime()) ? null : d;
  }

  function shortDate(d) {
    var opts = { day: 'numeric', month: 'short' };
    if (d.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
    return d.toLocaleDateString(undefined, opts);
  }

  function relativeTime(d) {
    var seconds = Math.round((Date.now() - d.getTime()) / 1000);
    if (seconds < 60) return 'just now';
    var minutes = Math.round(seconds / 60);
    if (minutes < 60) return minutes + 'm ago';
    var hours = Math.round(minutes / 60);
    if (hours < 24) return hours + 'h ago';
    var days = Math.round(hours / 24);
    if (days < 7) return days + 'd ago';
    return shortDate(d);
  }

  /* ---------------------------------------------------------- List render */

  function icon(paths, strokeWidth) {
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', strokeWidth || '1.7');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    paths.forEach(function (d) {
      var p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      p.setAttribute('d', d);
      svg.appendChild(p);
    });
    return svg;
  }

  function actionButton(action, label, paths) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'session-action';
    btn.setAttribute('data-action', action);
    btn.setAttribute('aria-label', label);
    btn.title = label;
    btn.appendChild(icon(paths));
    return btn;
  }

  // Titles come from a model or from the analyst, so every one of them is set with
  // textContent rather than interpolated into markup.
  function buildItem(session) {
    var li = document.createElement('li');
    li.className = 'session-item' + (session.id === activeId ? ' active' : '');
    li.setAttribute('data-id', session.id);

    var open = document.createElement('button');
    open.type = 'button';
    open.className = 'session-open';

    var name = document.createElement('span');
    name.className = 'session-name';
    name.textContent = session.title || 'Untitled session';
    open.appendChild(name);

    var meta = document.createElement('span');
    meta.className = 'session-meta';
    var created = parseTs(session.created_at);
    var updated = parseTs(session.updated_at);
    if (created) {
      var c = document.createElement('span');
      c.textContent = shortDate(created);
      c.title = 'Created ' + created.toLocaleString();
      meta.appendChild(c);
    }
    if (updated) {
      if (created) {
        var sep = document.createElement('span');
        sep.className = 'session-meta-sep';
        sep.textContent = '·';
        meta.appendChild(sep);
      }
      var u = document.createElement('span');
      u.textContent = 'edited ' + relativeTime(updated);
      u.title = 'Last modified ' + updated.toLocaleString();
      meta.appendChild(u);
    }
    open.appendChild(meta);
    open.title = session.activity_description || '';
    li.appendChild(open);

    var actions = document.createElement('span');
    actions.className = 'session-actions';
    actions.appendChild(actionButton('rename', 'Rename session',
      ['M12 20h9', 'M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z']));
    actions.appendChild(actionButton('delete', 'Delete session',
      ['M4 7h16', 'M10 11v6M14 11v6', 'M6 7l1 13h10l1-13', 'M9 7V4h6v3']));
    li.appendChild(actions);

    return li;
  }

  function render() {
    listEl.textContent = '';
    if (!sessions.length) {
      if (emptyEl) emptyEl.hidden = false;
      return;
    }
    if (emptyEl) emptyEl.hidden = true;
    sessions.forEach(function (s) { listEl.appendChild(buildItem(s)); });
  }

  function sortSessions() {
    sessions.sort(function (a, b) {
      return String(b.updated_at || '').localeCompare(String(a.updated_at || ''));
    });
  }

  /** Fold a server response back into the cached list without a second round trip. */
  function mergeSession(updated) {
    if (!updated || !updated.id) return;
    var found = false;
    sessions = sessions.map(function (s) {
      if (s.id !== updated.id) return s;
      found = true;
      return {
        id: updated.id,
        title: updated.title,
        activity_description: updated.activity_description,
        step_completed: updated.step_completed,
        created_at: updated.created_at,
        updated_at: updated.updated_at
      };
    });
    if (!found) sessions.push(updated);
    sortSessions();
    render();
  }

  function refresh() {
    return api('GET', '/api/sessions')
      .then(function (data) {
        sessions = data || [];
        clearListError();
        render();
        return sessions;
      })
      .catch(function (err) { showListError('load sessions', err); });
  }

  /* -------------------------------------------------------------- Saving */

  function noteChange() {
    if (suppressChanges) return;
    // Nothing to save into until an estimation has been started.
    if (!activeId && !pendingCreate) return;
    dirty = true;
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(function () { save(false); }, SAVE_DEBOUNCE_MS);
  }

  function save(keepalive) {
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
    if (!dirty) return Promise.resolve(null);
    var state = typeof window.getEstimationState === 'function' ? window.getEstimationState() : null;
    if (!state) return Promise.resolve(null);

    // Both of these are read now, not after the await. Switching or closing a session
    // clears activeId synchronously right after calling save(), so a save that read it
    // later would find null and silently drop the edits it was asked to flush.
    var targetId = activeId;
    var ready = pendingCreate || Promise.resolve(null);
    dirty = false;

    return ready.then(function () {
      // targetId is null when the session is still being created; activeId holds the
      // id by the time that promise resolves.
      var id = targetId || activeId;
      if (!id) { dirty = true; return null; }
      return api('PATCH', '/api/sessions/' + id, {
        structure: state.structure || null,
        step_completed: state.step_completed || null,
        activity_description: state.activity_description || '',
        activity_facts: state.activity_facts || null
      }, keepalive).then(mergeSession);
    }).catch(function (err) {
      dirty = true;  // let the next change retry it
      console.warn('[sessions] autosave failed:', err);
    });
  }

  /* ------------------------------------------------------------ Lifecycle */

  /** Run a script.js state change without it counting as an edit to save. */
  function withoutTracking(fn) {
    suppressChanges = true;
    try { if (typeof fn === 'function') fn(); }
    finally { suppressChanges = false; }
  }

  /**
   * Entry point for "Start estimation": a description that differs from the open
   * session's is new work and gets its own session; re-running the same text stays
   * where it is instead of littering the sidebar with duplicates.
   */
  function startEstimation(description, facts) {
    var current = sessions.filter(function (s) { return s.id === activeId; })[0];
    if (current && current.activity_description === description) {
      dirty = true;
      return Promise.resolve(current);
    }
    return beginSession(description, facts);
  }

  /** Called by script.js when the analyst starts an estimation on a description. */
  function beginSession(description, facts) {
    save(false);
    activeId = null;
    pendingCreate = api('POST', '/api/sessions', {
      activity_description: description,
      activity_facts: facts || null
    }).then(function (session) {
      pendingCreate = null;
      activeId = session.id;
      mergeSession(session);
      return session;
    }).catch(function (err) {
      pendingCreate = null;
      // The estimation itself is unaffected; it just will not be saved.
      showListError('create session', err);
      return null;
    });
    return pendingCreate;
  }

  function openSession(id) {
    if (id === activeId) return;
    save(false);
    api('GET', '/api/sessions/' + id)
      .then(function (session) {
        activeId = session.id;
        dirty = false;
        withoutTracking(function () { window.loadSessionState(session); });
        render();
      })
      .catch(function (err) { reportError('open session', err); });
  }

  function newSession() {
    save(false);
    activeId = null;
    dirty = false;
    withoutTracking(function () { window.resetEstimationState(); });
    render();
  }

  /* -------------------------------------------------------- Rename/delete */

  function askRename(id) {
    var session = sessions.filter(function (s) { return s.id === id; })[0];
    if (!session) return;
    pendingActionId = id;
    var input = document.getElementById('renameSessionInput');
    if (input) input.value = session.title || '';
    if (renameModal) renameModal.show();
    if (input) setTimeout(function () { input.focus(); input.select(); }, 200);
  }

  function confirmRename() {
    var input = document.getElementById('renameSessionInput');
    var title = input ? input.value.trim() : '';
    if (!title || !pendingActionId) return;
    var id = pendingActionId;
    if (renameModal) renameModal.hide();
    api('PATCH', '/api/sessions/' + id, { title: title })
      .then(mergeSession)
      .catch(function (err) { reportError('rename session', err); });
  }

  function askDelete(id) {
    var session = sessions.filter(function (s) { return s.id === id; })[0];
    if (!session) return;
    pendingActionId = id;
    var nameEl = document.getElementById('deleteSessionName');
    if (nameEl) nameEl.textContent = session.title || 'this session';
    if (deleteModal) deleteModal.show();
  }

  function confirmDelete() {
    if (!pendingActionId) return;
    var id = pendingActionId;
    if (deleteModal) deleteModal.hide();
    api('DELETE', '/api/sessions/' + id)
      .then(function () {
        sessions = sessions.filter(function (s) { return s.id !== id; });
        // Deleting the session on screen leaves the view showing work that is no
        // longer saved anywhere, so clear it rather than let it look persisted.
        if (id === activeId) {
          activeId = null;
          dirty = false;
          withoutTracking(function () { window.resetEstimationState(); });
        }
        render();
      })
      .catch(function (err) { reportError('delete session', err); });
  }

  /* --------------------------------------------------------------- Events */

  listEl.addEventListener('click', function (event) {
    var item = event.target.closest('.session-item');
    if (!item) return;
    var id = item.getAttribute('data-id');
    var actionBtn = event.target.closest('.session-action');
    if (actionBtn) {
      event.stopPropagation();
      var action = actionBtn.getAttribute('data-action');
      if (action === 'rename') askRename(id);
      else if (action === 'delete') askDelete(id);
      return;
    }
    if (event.target.closest('.session-open')) openSession(id);
  });

  if (btnNew) btnNew.addEventListener('click', newSession);

  function boot() {
    var renameEl = document.getElementById('renameSessionModal');
    var deleteEl = document.getElementById('deleteSessionModal');
    if (window.bootstrap && renameEl) renameModal = new bootstrap.Modal(renameEl);
    if (window.bootstrap && deleteEl) deleteModal = new bootstrap.Modal(deleteEl);

    var btnRename = document.getElementById('btnConfirmRename');
    if (btnRename) btnRename.addEventListener('click', confirmRename);
    var input = document.getElementById('renameSessionInput');
    if (input) input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); confirmRename(); }
    });
    var btnDelete = document.getElementById('btnConfirmDeleteSession');
    if (btnDelete) btnDelete.addEventListener('click', confirmDelete);

    refresh();
  }

  // These scripts are loaded at the end of <body>, so DOMContentLoaded is normally
  // still ahead of us — but check, so the sidebar does not silently stay empty if
  // the tag ever gains defer/async.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  // A tab closed or backgrounded mid-debounce would otherwise lose the last edit;
  // keepalive lets the request outlive the page.
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') save(true);
  });

  /** Title of the open session, or '' when none. Used to name exports. */
  function activeTitle() {
    var current = sessions.filter(function (s) { return s.id === activeId; })[0];
    return current && current.title ? current.title : '';
  }

  window.D2CSessions = {
    activeTitle: activeTitle,
    startEstimation: startEstimation,
    beginSession: beginSession,
    noteChange: noteChange,
    refresh: refresh,
    newSession: newSession,
    flush: function () { return save(false); },
    activeId: function () { return activeId; }
  };
})();
