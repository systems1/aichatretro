/* ask.js — RAG "Ask" panel. Depends on app.js globals (els, api, showToast,
 * renderMarkdown) and reads the service tabs' active service via a hook.
 * Loaded after app.js in index.html.
 */
(function () {
  'use strict';

  function $id(x) { return document.getElementById(x); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  var SERVICE_LABELS = { chatgpt: 'ChatGPT', deepseek: 'DeepSeek', gemini: 'Gemini' };
  var panel, content, input;

  function currentService() {
    // app.js stores state.service on window; expose via a small setter there.
    var svc = (window.appState && window.appState.service) || 'all';
    return svc;
  }

  function fetchJson(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (err) {
          throw new Error(err.error || ('HTTP ' + r.status));
        });
      }
      return r.json();
    });
  }

  function hideAsk() {
    if (panel) panel.hidden = true;
  }

  function showAskLoading() {
    content.innerHTML = '';
    content.appendChild(el('div', 'ask-loading', 'Searching your chat history…'));
  }

  function sourceChip(s) {
    var chip = el('a', 'ask-source');
    var dot = el('span', 'dot');
    dot.style.background = (window.SERVICE_COLORS && window.SERVICE_COLORS[s.service]) || '#888';
    chip.appendChild(dot);
    chip.appendChild(document.createTextNode((s.conversation_title || '(untitled)').slice(0, 60)));
    chip.href = '#';
    chip.addEventListener('click', function (e) {
      e.preventDefault();
      hideAsk();
      if (window.openConversation) {
        window.openConversation({ service: s.service, id: s.conversation_id, title: s.conversation_title });
      }
    });
    return chip;
  }

  function renderSources(sources) {
    var wrap = el('div', 'ask-sources');
    wrap.appendChild(el('div', 'ask-sources-label', 'Sources:'));
    sources.forEach(function (s) { wrap.appendChild(sourceChip(s)); });
    return wrap;
  }

  function showAskResult(data) {
    content.innerHTML = '';

    if (data.status && data.status.built === false) {
      content.appendChild(el('div', 'ask-error',
        'RAG is not available. Install optional deps:  pip install -r requirements-rag.txt'));
      return;
    }
    if (data.error) {
      content.appendChild(el('div', 'ask-error', data.error));
      return;
    }

    if (data.answer) {
      var md = el('div', 'md');
      md.innerHTML = (window.renderMarkdown || function (t) { return t; })(data.answer);
      content.appendChild(md);
    } else {
      content.appendChild(el('div', 'ask-note',
        'Retrieved sources below (install a local LLM for generated answers).'));
    }

    if (data.sources && data.sources.length) {
      content.appendChild(renderSources(data.sources));
    } else {
      content.appendChild(el('div', 'ask-note', 'No relevant messages found.'));
    }
  }

  function ask(q) {
    if (!q) return;
    panel.hidden = false;
    showAskLoading();
    var svc = currentService();
    var url = '/api/ask?q=' + encodeURIComponent(q) +
      (svc !== 'all' ? '&service=' + encodeURIComponent(svc) : '');
    fetchJson(url).then(showAskResult).catch(function (e) {
      content.innerHTML = '';
      content.appendChild(el('div', 'ask-error', e.message));
      if (window.showToast) window.showToast(e.message);
    });
  }

  function init() {
    panel = $id('ask-panel');
    content = $id('ask-content');
    input = $id('ask-input');
    if (!panel || !content || !input) return;

    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        ask(input.value.trim());
        input.value = '';
      }
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();