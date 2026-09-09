/* app.js — AI Chat Retro SPA. Depends on markdown.js (renderMarkdown). */
(function () {
  'use strict';

  var state = {
    service: 'all',
    search: '',
    services: [],
    list: [],
    total: 0,
    selectedKey: null,
    conv: null,
    searchMode: false
  };

  var els = {};
  var SERVICE_COLORS = { chatgpt: '#10a37f', deepseek: '#4d6bfe', gemini: '#a66cff' };
  var SERVICE_LABELS = { chatgpt: 'ChatGPT', deepseek: 'DeepSeek', gemini: 'Gemini' };

  function $(id) { return document.getElementById(id); }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function safeExternalUrl(value) {
    var url = String(value || '');
    return /^https?:\/\//i.test(url) ? url : null;
  }

  function fmtDate(ts) {
    if (!ts) return '—';
    return new Date(ts * 1000).toLocaleString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit'
    });
  }

  function relTime(ts) {
    if (!ts) return '';
    var diff = Date.now() / 1000 - ts;
    if (diff < 60) return 'just now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    if (diff < 86400 * 30) return Math.floor(diff / 86400) + 'd ago';
    if (diff < 86400 * 365) return Math.floor(diff / (86400 * 30)) + ' mo ago';
    return new Date(ts * 1000).toLocaleDateString(undefined, { year: 'numeric', month: 'short' });
  }

  function api(path) {
    return fetch(path).then(function (r) {
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (err) {
          throw new Error(err.error || ('HTTP ' + r.status));
        });
      }
      return r.json();
    });
  }

  /* ---------------------------------------------------------------- Init */
  function captureEls() {
    els.tabs = $('service-tabs');
    els.searchInput = $('search-input');
    els.statsBtn = $('stats-btn');
    els.backBtn = $('back-btn');
    els.list = $('conv-list');
    els.listStatus = $('list-status');
    els.chatHeader = $('chat-header');
    els.messageList = $('message-list');
    els.empty = $('empty-state');
    els.statsOverlay = $('stats-overlay');
    els.statsContent = $('stats-content');
    els.statsClose = $('stats-close');
    els.lightbox = $('lightbox');
    els.lightboxImg = $('lightbox-img');
    els.lightboxClose = $('lightbox-close');
    els.toast = $('toast');
  }

  function bindEvents() {
    els.searchInput.addEventListener('input', function () {
      clearTimeout(state._st);
      state._st = setTimeout(function () {
        state.search = els.searchInput.value.trim();
        refreshList();
      }, 250);
    });
    els.searchInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') globalSearch(els.searchInput.value.trim());
    });
    els.statsBtn.addEventListener('click', openStats);
    els.statsClose.addEventListener('click', closeStats);
    els.statsOverlay.addEventListener('click', function (e) {
      if (e.target === els.statsOverlay) closeStats();
    });
    els.backBtn.addEventListener('click', function () {
      state.searchMode = false;
      state.conv = null;
      state.selectedKey = null;
      els.backBtn.style.display = 'none';
      renderSidebar();
      showEmpty();
    });
    els.lightboxClose.addEventListener('click', closeLightbox);
    els.lightbox.addEventListener('click', function (e) {
      if (e.target === els.lightbox) closeLightbox();
    });
    /* A broken image must never leave the user trapped in a black lightbox. */
    els.lightboxImg.addEventListener('error', closeLightbox);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { closeLightbox(); closeStats(); }
    });
  }

  function init() {
    captureEls();
    bindEvents();
    loadServices();
    refreshList();
  }

  function loadServices() {
    api('/api/services').then(function (data) {
      state.services = data.services;
      renderTabs();
    }).catch(showToast);
  }

  function renderTabs() {
    var all = state.services.reduce(function (s, x) { return s + x.count; }, 0);
    els.tabs.innerHTML = '';
    addTab('all', 'All', all);
    state.services.forEach(function (svc) {
      addTab(svc.service, svc.label, svc.count);
    });
  }

  function addTab(key, label, count) {
    var b = el('button', 'tab' + (state.service === key ? ' active' : ''));
    var dot = el('span', 'dot');
    dot.style.background = SERVICE_COLORS[key] || '#888';
    b.appendChild(dot);
    b.appendChild(document.createTextNode(label + ' '));
    var num = el('strong', 'count', count);
    b.appendChild(num);
    b.addEventListener('click', function () {
      state.service = key;
      renderTabs();
      refreshList();
    });
    els.tabs.appendChild(b);
  }

  /* --------------------------------------------------------------- Sidebar */
  function refreshList() {
    var params = new URLSearchParams({ service: state.service, limit: '1000' });
    if (state.search) params.set('search', state.search);
    api('/api/conversations?' + params.toString()).then(function (data) {
      state.total = data.total;
      state.list = data.conversations;
      listStatus();
      renderSidebar();
    }).catch(showToast);
  }

  function listStatus() {
    els.listStatus.textContent = (state.search ? 'Matches: ' : 'Chats: ') + state.total;
  }

  function renderSidebar() {
    if (state.searchMode) return;
    els.list.innerHTML = '';
    var frag = document.createDocumentFragment();
    state.list.forEach(function (s) { frag.appendChild(sidebarItem(s)); });
    els.list.appendChild(frag);
    if (!state.list.length) {
      els.list.appendChild(el('div', 'list-empty', state.search ? 'No matching chats.' : 'No exports found.'));
    }
  }

  function sidebarItem(s) {
    var li = el('li', 'conv-item' + (state.selectedKey === s.service + '/' + s.id ? ' active' : ''));
    var dot = el('span', 'dot');
    dot.style.background = SERVICE_COLORS[s.service] || '#888';
    li.appendChild(dot);
    var main = el('div', 'conv-main');
    main.appendChild(el('div', 'conv-title', s.title || '(untitled)'));
    main.appendChild(el('div', 'conv-meta',
      SERVICE_LABELS[s.service] + ' · ' + relTime(s.updated_at) + ' · ' + s.msg_count + ' msgs'));
    li.appendChild(main);
    li.addEventListener('click', function () { openConversation(s); });
    return li;
  }

  /* ---------------------------------------------------------- Conversation */
  function openConversation(summary) {
    state.searchMode = false;
    state.selectedKey = summary.service + '/' + summary.id;
    renderSidebar();
    showLoading();
    api('/api/conversation/' + summary.service + '/' + encodeURIComponent(summary.id))
      .then(function (conv) {
        if (state.selectedKey !== summary.service + '/' + summary.id) return;
        state.conv = conv;
        renderConversation();
      })
      .catch(function (e) { showToast(e.message); showEmpty(); });
  }

  function renderConversation() {
    var conv = state.conv;
    if (!conv) return;
    hideEmpty();
    els.backBtn.style.display = 'inline-flex';
    els.chatHeader.innerHTML = '';
    els.chatHeader.appendChild(el('div', 'chat-title', conv.title || '(untitled)'));
    els.chatHeader.appendChild(el('div', 'chat-meta',
      SERVICE_LABELS[conv.service] + ' · created ' + fmtDate(conv.created_at) +
      ' · updated ' + fmtDate(conv.updated_at) + ' · ' + conv.messages.length + ' messages' +
      (conv.model ? ' · ' + conv.model : '')));

    var msgs = conv.messages;
    var carry = msgs.length > 400 ? msgs.slice(0, msgs.length - 200) : [];
    var show = msgs.length > 400 ? msgs.slice(-200) : msgs;

    els.messageList.innerHTML = '';
    if (carry.length) {
      var prev = el('button', 'load-more', 'Load earlier (' + carry.length + ' messages)');
      prev.addEventListener('click', function () {
        var batch = carry.slice(-200);
        var nodes = batch.map(renderMessage);
        for (var k = nodes.length - 1; k >= 0; k--) {
          els.messageList.insertBefore(nodes[k], els.messageList.firstChild);
        }
        carry = carry.slice(0, carry.length - 200);
        if (carry.length) {
          prev.textContent = 'Load earlier (' + carry.length + ' messages)';
        } else {
          prev.remove();
        }
      });
      els.messageList.appendChild(prev);
    }
    var frag = document.createDocumentFragment();
    show.forEach(function (m) { frag.appendChild(renderMessage(m)); });
    els.messageList.appendChild(frag);
    els.messageList.scrollTop = els.messageList.scrollHeight;
  }

  function renderMessage(m) {
    var role = m.role === 'assistant' ? 'assistant' : (m.role === 'system' ? 'system' : 'user');
    var wrap = el('div', 'msg ' + role);
    var row = el('div', 'msg-row');
    var label = state.conv ? (SERVICE_LABELS[state.conv.service] || 'A') : 'A';
    row.appendChild(el('div', 'avatar', role === 'user' ? 'U' : label[0]));

    var bubble = el('div', 'bubble');

    if (m.thoughts) bubble.appendChild(reasoningBlock(m.thoughts));
    if (m.recap) bubble.appendChild(el('div', 'recap', 'Reflection: ' + m.recap));

    if (m.audio_transcript) {
      bubble.appendChild(el('div', 'voice', 'Voice message: "' + m.audio_transcript + '"'));
    }

    if (m.content) {
      var md = el('div', 'md');
      md.innerHTML = renderMarkdown(m.content);
      bubble.appendChild(md);
    }

    if (m.images && m.images.length) {
      var imgRow = el('div', 'img-row');
      m.images.forEach(function (img) {
        if (!img.src) return;
        var fig = el('figure', 'img-fig');
        var im = document.createElement('img');
        im.src = img.src;
        im.alt = img.name || '';
        im.loading = 'lazy';
        im.addEventListener('click', function () { openLightbox(img.src, img.name); });
        fig.appendChild(im);
        if (img.name) fig.appendChild(el('figcaption', 'img-cap', img.name));
        imgRow.appendChild(fig);
      });
      if (imgRow.children.length) bubble.appendChild(imgRow);
    }

    if (m.attachments && m.attachments.length) {
      var attRow = el('div', 'att-row');
      m.attachments.forEach(function (a) {
        var chip = el('a', 'chip', a.name || 'file');
        if (a.src) { chip.href = a.src; chip.target = '_blank'; }
        attRow.appendChild(chip);
      });
      if (attRow.children.length) bubble.appendChild(attRow);
    }

    if ((m.citations && m.citations.length) || (m.searchResults && m.searchResults.length)) {
      bubble.appendChild(citationsBlock(m.citations || [], m.searchResults || []));
    }

    var foot = el('div', 'msg-foot');
    var pieces = [];
    if (m.model) pieces.push(m.model);
    if (m.time) pieces.push(fmtDate(m.time) + ' · ' + relTime(m.time));
    foot.textContent = pieces.join(' · ');
    bubble.appendChild(foot);

    row.appendChild(bubble);
    wrap.appendChild(row);
    return wrap;
  }

  function reasoningBlock(text) {
    var box = el('div', 'thoughts');
    var head = el('button', 'thoughts-head', 'Reasoning ▸');
    var body = el('div', 'thoughts-body hidden');
    body.appendChild(el('pre', 'thoughts-pre', text));
    head.addEventListener('click', function () {
      body.classList.toggle('hidden');
      head.textContent = body.classList.contains('hidden') ? 'Reasoning ▸' : 'Reasoning ▾';
    });
    box.appendChild(head);
    box.appendChild(body);
    return box;
  }

  function citationsBlock(cites, searchRes) {
    var all = cites.concat(searchRes);
    if (!all.length) return el('div');
    var box = el('div', 'cites');
    var head = el('button', 'cites-head', 'Sources (' + all.length + ') ▸');
    var body = el('div', 'cites-body hidden');
    all.forEach(function (c) {
      var a = el('a', 'cite', '');
      var t = el('div', 'cite-title', c.title || c.url);
      var s = el('div', 'cite-url', c.url);
      a.appendChild(t);
      a.appendChild(s);
      var url = safeExternalUrl(c.url);
      if (url) {
        a.href = url;
        a.target = '_blank';
        a.rel = 'noopener';
      }
      body.appendChild(a);
    });
    head.addEventListener('click', function () {
      body.classList.toggle('hidden');
      var open = !body.classList.contains('hidden');
      head.textContent = 'Sources (' + all.length + ') ' + (open ? '▾' : '▸');
    });
    box.appendChild(head);
    box.appendChild(body);
    return box;
  }

  /* ---------------------------------------------------------- Global search */
  function globalSearch(q) {
    if (!q) return;
    api('/api/search?q=' + encodeURIComponent(q)).then(function (data) {
      els.backBtn.style.display = 'inline-flex';
      state.searchMode = true;
      showSearchResults(data);
    }).catch(showToast);
  }

  function showSearchResults(data) {
    hideEmpty();
    state.conv = null;
    els.chatHeader.innerHTML = '';
    els.chatHeader.appendChild(el('div', 'chat-title',
      'Search: "' + data.query + '" — ' + data.total + ' chats'));
    els.messageList.innerHTML = '';

    var list = el('div', 'search-results');
    data.results.forEach(function (r) {
      var it = el('div', 'search-item');
      var dot = el('span', 'dot');
      dot.style.background = SERVICE_COLORS[r.service] || '#888';
      it.appendChild(dot);
      var body = el('div');
      body.appendChild(el('div', 'search-title', r.title || '(untitled)'));
      body.appendChild(el('div', 'search-snippet', r.snippet || ''));
      body.appendChild(el('div', 'search-meta', SERVICE_LABELS[r.service] + ' · ' + fmtDate(r.updated_at)));
      it.appendChild(body);
      it.addEventListener('click', function () {
        openConversation({ service: r.service, id: r.conversation_id, title: r.title });
      });
      list.appendChild(it);
    });
    if (!data.results.length) list.appendChild(el('div', 'list-empty', 'No matches.'));
    els.messageList.appendChild(list);
  }

  /* ------------------------------------------------------------------- Stats */
  function openStats() {
    api('/api/stats').then(function (data) {
      els.statsContent.innerHTML = '';
      els.statsContent.appendChild(el('h2', '', 'Statistics'));
      els.statsContent.appendChild(el('p', 'stat-total', 'Total conversations: ' + data.total_conversations));
      data.services.forEach(function (s) {
        var row = el('div', 'stat-row');
        var dot = el('span', 'dot');
        dot.style.background = SERVICE_COLORS[s.service] || '#888';
        row.appendChild(dot);
        row.appendChild(el('span', '',
          s.label + ': ' + s.count + '  (' + fmtDate(s.first_ts) + ' — ' + fmtDate(s.last_ts) + ')'));
        els.statsContent.appendChild(row);
      });
      if (data.models && data.models.length) {
        els.statsContent.appendChild(el('h3', '', 'Model usage'));
        data.models.slice(0, 12).forEach(function (md) {
          els.statsContent.appendChild(el('div', 'model-row',
            md.model + ' (' + SERVICE_LABELS[md.service] + ') — ' + md.count));
        });
      }
      els.statsOverlay.hidden = false;
    }).catch(showToast);
  }

  function closeStats() { els.statsOverlay.hidden = true; }

  /* ---------------------------------------------------------------- Lightbox */
  function openLightbox(src, name) {
    els.lightboxImg.src = src;
    els.lightboxImg.alt = name || '';
    els.lightbox.hidden = false;
  }
  function closeLightbox() {
    els.lightbox.hidden = true;
    els.lightboxImg.src = '';
  }

  /* --------------------------------------------------------------- Helpers */
  function showEmpty() {
    els.backBtn.style.display = 'none';
    els.empty.style.display = '';
    els.chatHeader.innerHTML = '';
    els.messageList.innerHTML = '';
  }
  function hideEmpty() { els.empty.style.display = 'none'; }

  function showLoading() {
    hideEmpty();
    els.chatHeader.innerHTML = '';
    els.messageList.innerHTML = '';
    els.messageList.appendChild(el('div', 'loading', 'Loading…'));
  }

  var toastTimer;
  function showToast(msg) {
    els.toast.textContent = msg || 'Something went wrong';
    els.toast.className = 'toast show';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { els.toast.className = 'toast'; }, 3000);
  }

  /* Loud error reporting — a failed boot should never be a silent dark page. */
  function reportFatal(msg) {
    var box = document.getElementById('startup-errors');
    if (!box) return;
    box.hidden = false;
    box.textContent = msg;
  }
  /* Expose minimal hooks for the optional RAG ask.js panel. */
  window.appState = state;
  window.SERVICE_COLORS = SERVICE_COLORS;
  window.openConversation = openConversation;

  window.addEventListener('error', function (e) {
    reportFatal('⚠️ Startup error: ' + (e.message || 'unknown') +
      ' (' + String(e.filename || '').split('/').pop() + ':' + (e.lineno || '?') + ').' +
      '\n→ Hard refresh (Ctrl+F5) to clear old cached files. If it persists, the server must be running:  python3 app.py');
  }, true);
  window.addEventListener('unhandledrejection', function (e) {
    reportFatal('⚠️ Network/request problem: ' +
      ((e.reason && e.reason.message) ? e.reason.message : String(e.reason || 'request failed')) +
      '\n→ Make sure python3 app.py is running and reachable, then refresh.');
  });
  document.addEventListener('DOMContentLoaded', function () {
    try { init(); }
    catch (err) {
      reportFatal('⚠️ App crashed on start: ' + (err && err.message) +
        '\n→ Try Ctrl+F5. If it persists, restart the server (python3 app.py).');
    }
  });
})();
