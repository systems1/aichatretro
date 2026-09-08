/* markdown.js — minimal offline Markdown renderer. No dependencies, no CDN.
 * Global: renderMarkdown(md) -> HTML string.
 *
 * Scope: fenced + indented code blocks, headings, hr, blockquote, bullet+ordered
 * lists, tables, paragraphs, bold/italic/strike, inline code, links, images.
 * Everything is HTML-escaped first; code blocks are extracted to
 * E000N E000 placeholder tokens so inline passes never mutate code.
 * NOTE: token regexes must NOT share a /g regex across .test() calls
 * (lastIndex state) — test-regex is non-global.
 */
(function (global) {
  'use strict';

  var P = String.fromCharCode(0xE000);                 // token delimiter (private-use char)
  var TOKEN_RE = new RegExp(P + '(\\d+)' + P, 'g');    // used only for final reinsert
  var TOKEN_TEST_RE = new RegExp(P + '(\\d+)' + P);    // non-global: .test() is stateless

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /* ------------------------------------------------------------------ *
   * Code extraction: fenced (``` / ~~~) + indented (4-space) blocks
   * ------------------------------------------------------------------ */
  function extractCode(src) {
    var blocks = [];
    var s = String(src);

    function hold(html) {
      blocks.push(html);
      return P + (blocks.length - 1) + P;
    }

    // Fenced blocks: ```lang\n ... \n```
    s = s.replace(/^(`{3,}|~{3,})[ \t]*([^\n]*)\n([\s\S]*?)\n[ \t]*\1[ \t]*$/gm,
      function (whole, fence, info, code) {
        var lang = info ? ' class="lang-' + escapeHtml(info.trim()) + '"' : '';
        return hold('<pre><code' + lang + '>' +
          escapeHtml(code.replace(/\n$/, '')) + '</code></pre>');
      });

    // Indented blocks: consecutive lines starting with 4 spaces
    s = s.replace(/^(?: {4}[^\n]*(?:\n|$))+/gm, function (m) {
      return hold('<pre><code>' + escapeHtml(m.replace(/^ {4}/gm, '')) + '</code></pre>');
    });

    return { text: s, blocks: blocks };
  }

  /* ------------------------------------------------------------------ *
   * Inline pass (operates on already-escaped text; code is held aside)
   * ------------------------------------------------------------------ */
  function renderInline(s) {
    if (!s) return '';

    // images before links
    s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g,
      function (m, alt, url) { return '<img src="' + url + '" alt="' + alt + '">'; });

    // links: [text](url)
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/(?:(?!\))[^'"<>])+)\)/g,
      function (m, text, url) { return '<a href="' + url + '" rel="noopener">' + text + '</a>'; });

    // inline code
    s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');

    // bold
    s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/__([^_\n]+)__/g, '<strong>$1</strong>');

    // strike
    s = s.replace(/~~([^~\n]+)~~/g, '<s>$1</s>');

    // italic (single asterisk/underscore not part of bold)
    s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
    s = s.replace(/(^|[^_\w])_([^_\n]+)_(?!_)/g, '$1<em>$2</em>');

    return s;
  }

  /* ------------------------------------------------------------------ *
   * Block pass
   * ------------------------------------------------------------------ */
  function blockPass(text, blocks) {
    var lines = text.split('\n');
    var out = [];
    var i = 0;
    var n = lines.length;

    function tokens(html) { out.push(html); }
    function inline(parts) { return renderInline(parts.join('\n')); }
    function isToken(line) { return TOKEN_TEST_RE.test(line); }

    var liRe = /^(?!\s)([-*+]|\d+\.)\s+(.*)$/;

    while (i < n) {
      var line = lines[i];

      if (!line.trim()) { i++; continue; }               // blank
      if (isToken(line)) { tokens(line); i++; continue; } // held code

      if (/^(?:\*\s*){3,}$/.test(line) ||
          /^(?:-\s*){3,}$/.test(line) ||
          /^(?:_\s*){3,}$/.test(line)) { tokens('<hr>'); i++; continue; } // hr

      var h = /^(#{1,6})\s+(.*)$/.exec(line);             // heading
      if (h) {
        var lvl = h[1].length;
        tokens('<h' + lvl + '>' + inline([h[2]]) + '</h' + lvl + '>');
        i++;
        continue;
      }

      if (/^>\s?/.test(line)) {                           // blockquote
        var q = [];
        while (i < n && /^>\s?/.test(lines[i])) {
          q.push(lines[i].replace(/^>\s?/, ''));
          i++;
        }
        tokens('<blockquote>' + inline(q) + '</blockquote>');
        continue;
      }

      var lm = liRe.exec(line);                           // list
      if (lm) {
        var ordered = /^\d+\./.test(lm[1]);
        var items = [];
        while (i < n) {
          var m2 = liRe.exec(lines[i]);
          if (!m2 || /^\d+\./.test(m2[1]) !== ordered) break;
          items.push(m2[2]);
          i++;
        }
        var tag = ordered ? 'ol' : 'ul';
        tokens('<' + tag + '>' + items.map(function (it) {
          return '<li>' + inline([it]) + '</li>';
        }).join('') + '</' + tag + '>');
        continue;
      }

      if (/^\|.*\|/.test(line) && i + 1 < n &&            // table
          /^[\s:|-]+$/.test(lines[i + 1].replace(/\|/g, '').trim()) &&
          lines[i + 1].indexOf('-') >= 0) {
        var headerCells = line.split('|').slice(1, -1).map(function (c) { return c.trim(); });
        i += 2;
        var bodyRows = [];
        while (i < n && /^\|.*\|/.test(lines[i])) {
          bodyRows.push(lines[i].split('|').slice(1, -1).map(function (c) { return c.trim(); }));
          i++;
        }
        var th = headerCells.map(function (c) { return '<th>' + inline([c]) + '</th>'; }).join('');
        var trs = bodyRows.map(function (r) {
          return '<tr>' + r.map(function (c) { return '<td>' + inline([c]) + '</td>'; }).join('') + '</tr>';
        }).join('');
        tokens('<table><thead><tr>' + th + '</tr></thead><tbody>' + trs + '</tbody></table>');
        continue;
      }

      // paragraph: accumulate until a blank line or another block start
      var para = [line];
      i++;
      while (i < n && lines[i].trim() &&
             !/^(#{1,6})\s/.test(lines[i]) &&
             !liRe.test(lines[i]) &&
             !/^>\s?/.test(lines[i]) &&
             !isToken(lines[i])) {
        para.push(lines[i]);
        i++;
      }
      tokens('<p>' + inline(para) + '</p>');
    }

    // Reinsert held code blocks.
    return out.join('\n').replace(TOKEN_RE, function (m, idx) {
      return blocks[+idx] || m;
    });
  }

  function renderMarkdown(md) {
    if (md == null) return '';
    var extracted = extractCode(md);
    return blockPass(extracted.text, extracted.blocks).replace(/\n{3,}/g, '\n\n');
  }

  global.renderMarkdown = renderMarkdown;
  global.markdownReady = true;
})(window);
