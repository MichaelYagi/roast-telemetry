// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Turns the Markdown a language model writes (headings, lists, tables, code,
// links, a little LaTeX) into HTML for the AI review box. Self-contained: no
// libraries, nothing loaded from the internet.
//
// Link and image text is escaped when it is put into the page, so text in a
// review can't add its own HTML or attributes.

const escapeHtml = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

export function formatMessage(text) {
    const blocks = [], inline = [], links = [], mathInline = [], mathBlock = [];

    // Extract delimited math FIRST so its content is protected from sanitization.
    text = text.replace(/\$\$([\s\S]*?)\$\$/g, (m, math) => { const i = mathBlock.length; mathBlock.push(math); return `@@MATHBLOCK_${i}@@`; });
    text = text.replace(/\\\[([\s\S]*?)\\\]/g, (m, math) => { const i = mathBlock.length; mathBlock.push(math); return `@@MATHBLOCK_${i}@@`; });
    text = text.replace(/\\\(([\s\S]*?)\\\)/g, (m, math) => { const i = mathInline.length; mathInline.push(math); return `@@MATHINLINE_${i}@@`; });
    // Single $...$ inline math — extracted after $$ so double-dollar is already consumed
    text = text.replace(/(?<!\$)\$(?!\$)([^$\n]+?)\$(?!\$)/g, (m, math) => { const i = mathInline.length; mathInline.push(math); return `@@MATHINLINE_${i}@@`; });
    // \begin{env}...\end{env} — treat any LaTeX environment as a display math block
    text = text.replace(/\\begin\{([a-zA-Z*]+)\}([\s\S]*?)\\end\{\1\}/g, (m, env, content) => { const i = mathBlock.length; mathBlock.push(content); return `@@MATHBLOCK_${i}@@`; });

    // Extract code blocks and inline code before sanitizer.
    text = text.replace(/([ \t]*)```(\w+)?\s*\n?([\s\S]*?)```/g, (m, indent, lang, code) => {
        const indentLen = indent.length, lines = code.split('\n');
        const dedented = lines.map(l => l.trim().length === 0 ? '' : l.startsWith(indent) ? l.slice(indentLen) : l.trimStart()).join('\n').trim();
        const i = blocks.length; blocks.push({ lang: lang || "code", code: dedented }); return `@@CODEBLOCK_${i}@@`;
    });
    text = text.replace(/'''([\s\S]*?)'''/g, (m, code) => { const i = blocks.length; blocks.push({ lang: "code", code }); return `@@CODEBLOCK_${i}@@`; });
    text = text.replace(/`([^`]+)`/g, (m, code) => { const i = inline.length; inline.push(code); return `@@INLINE_${i}@@`; });

    // Unescape literal \n and \t from tool output (placeholders are already safe).
    text = text.replace(/\\n/g, '\n').replace(/\\t/g, '\t');

    // -- Bare LaTeX sanitizer --------------------------------------------
    // Models emit LaTeX two ways:
    //   a) Proper backslash: \times  -- matched by /\\times/ (two backslashes in regex literal)
    //   b) Tab-corrupted:   tab+imes -- matched by /\times/ (\t=tab in JS regex)
    //
    // IMPORTANT: tab-corrupted patterns are only safe for keywords beginning with 't',
    // because only \t produces a meaningful JS regex escape (tab char). Other letters
    // produce regex literals that match normal English words (\omega -> /omega/, etc.)
    // So tab patterns are used ONLY for: \text, \times, \theta.
    //
    // All patterns use (?<![a-zA-Z]) lookbehind to avoid firing inside words.

    // \text{...} -> content  (\text and tab-corrupted tab+ext)
    text = text.replace(/(?<![a-zA-Z])\\text\s*\{([^}]*)\}/g, '$1');
    text = text.replace(/(?<![a-zA-Z])\text\s*\{([^}]*)\}/g, '$1');

    // \frac{a}{b} -> (a)/(b)  (proper backslash only — \f=formfeed so tab form unreliable)
    text = text.replace(/(?<![a-zA-Z])\\frac\s*\{([^}]*)\}\s*\{([^}]*)\}/g, '($1)/($2)');
    // Second pass: resolve symbols that survived inside \frac braces
    text = text.replace(/(?<![a-zA-Z])\\pi(?![a-zA-Z])/g,    '\u03c0');
    text = text.replace(/(?<![a-zA-Z])\\omega(?![a-zA-Z])/g, '\u03c9');
    text = text.replace(/(?<![a-zA-Z])\\alpha(?![a-zA-Z])/g, '\u03b1');
    text = text.replace(/(?<![a-zA-Z])\\beta(?![a-zA-Z])/g,  '\u03b2');

    // \sqrt
    text = text.replace(/(?<![a-zA-Z])\\sqrt\s*\[([^\]]+)\]\s*\{([^}]+)\}/g, '<sup>$1</sup>\u221a($2)');
    text = text.replace(/(?<![a-zA-Z])\\sqrt\s*\{([^}]*)\}/g, '\u221a($1)');

    // \left / \right  (proper backslash only — /\left/ matches "left" in prose)
    text = text.replace(/\\left\s*\(/g, '(').replace(/\\right\s*\)/g, ')');
    text = text.replace(/\\left\s*\[/g, '[').replace(/\\right\s*\]/g, ']');
    text = text.replace(/\\left\s*\{/g, '{').replace(/\\right\s*\}/g, '}');

    text = text.replace(/\^\{([^}]+)\}/g, '^$1');
    text = text.replace(/_\{([^}]+)\}/g, '_$1');

    // Single symbols — proper backslash form only, except \times and \theta which also
    // get the tab-corrupted form (safe because \t in regex = tab, not a common substring).
    // All use (?<![a-zA-Z]) lookbehind to avoid collisions inside words.
    text = text.replace(/(?<![a-zA-Z])\\times(?![a-zA-Z])/g,     '\u00d7');
    text = text.replace(/(?<![a-zA-Z])\times(?![a-zA-Z])/g,      '\u00d7');
    text = text.replace(/(?<![a-zA-Z])\\theta(?![a-zA-Z])/g,     '\u03b8');
    text = text.replace(/(?<![a-zA-Z])\theta(?![a-zA-Z])/g,      '\u03b8');
    text = text.replace(/(?<![a-zA-Z])\\cdot(?![a-zA-Z])/g,      '\u00b7');
    text = text.replace(/(?<![a-zA-Z])\\div(?![a-zA-Z])/g,       '\u00f7');
    text = text.replace(/(?<![a-zA-Z])\\pm(?![a-zA-Z])/g,        '\u00b1');
    text = text.replace(/(?<![a-zA-Z])\\approx(?![a-zA-Z])/g,    '\u2248');
    text = text.replace(/(?<![a-zA-Z])\\neq(?![a-zA-Z])/g,       '\u2260');
    text = text.replace(/(?<![a-zA-Z])\\leq(?![a-zA-Z])/g,       '\u2264');
    text = text.replace(/(?<![a-zA-Z])\\geq(?![a-zA-Z])/g,       '\u2265');
    text = text.replace(/(?<![a-zA-Z])\\infty(?![a-zA-Z])/g,     '\u221e');
    text = text.replace(/(?<![a-zA-Z])\\pi(?![a-zA-Z])/g,        '\u03c0');
    text = text.replace(/(?<![a-zA-Z])\\omega(?![a-zA-Z])/g,     '\u03c9');
    text = text.replace(/(?<![a-zA-Z])\\alpha(?![a-zA-Z])/g,     '\u03b1');
    text = text.replace(/(?<![a-zA-Z])\\beta(?![a-zA-Z])/g,      '\u03b2');
    text = text.replace(/(?<![a-zA-Z])\\gamma(?![a-zA-Z])/g,     '\u03b3');
    text = text.replace(/(?<![a-zA-Z])\\sigma(?![a-zA-Z])/g,     '\u03c3');
    text = text.replace(/(?<![a-zA-Z])\\lambda(?![a-zA-Z])/g,    '\u03bb');
    text = text.replace(/(?<![a-zA-Z])\\mu(?![a-zA-Z])/g,        '\u03bc');
    text = text.replace(/(?<![a-zA-Z])\\Delta(?![a-zA-Z])/g,     '\u0394');
    text = text.replace(/(?<![a-zA-Z])\\Sigma(?![a-zA-Z])/g,     '\u03a3');
    text = text.replace(/(?<![a-zA-Z])\\Omega(?![a-zA-Z])/g,     '\u03a9');
    text = text.replace(/(?<![a-zA-Z])\\phi(?![a-zA-Z])/g,       '\u03c6');
    text = text.replace(/(?<![a-zA-Z])\\Phi(?![a-zA-Z])/g,       '\u03a6');
    text = text.replace(/(?<![a-zA-Z])\\psi(?![a-zA-Z])/g,       '\u03c8');
    text = text.replace(/(?<![a-zA-Z])\\rightarrow(?![a-zA-Z])/g,'\u2192');
    text = text.replace(/(?<![a-zA-Z])\\leftarrow(?![a-zA-Z])/g, '\u2190');
    text = text.replace(/(?<![a-zA-Z])\\Rightarrow(?![a-zA-Z])/g,'\u21d2');
    text = text.replace(/(?<![a-zA-Z])\\Leftarrow(?![a-zA-Z])/g, '\u21d0');
    text = text.replace(/(?<![a-zA-Z])\\forall(?![a-zA-Z])/g,    '\u2200');
    text = text.replace(/(?<![a-zA-Z])\\exists(?![a-zA-Z])/g,    '\u2203');
    text = text.replace(/(?<![a-zA-Z])\\degree(?![a-zA-Z])/g,    '\u00b0');
    text = text.replace(/(?<![a-zA-Z])\\circ(?![a-zA-Z])/g,      '\u00b0');

    // Calculus / set / logic operators
    text = text.replace(/(?<![a-zA-Z])\\sum(?![a-zA-Z])/g,       '\u2211');
    text = text.replace(/(?<![a-zA-Z])\\prod(?![a-zA-Z])/g,      '\u220f');
    text = text.replace(/(?<![a-zA-Z])\\iiint(?![a-zA-Z])/g,     '\u222d');
    text = text.replace(/(?<![a-zA-Z])\\iint(?![a-zA-Z])/g,      '\u222c');
    text = text.replace(/(?<![a-zA-Z])\\oint(?![a-zA-Z])/g,      '\u222e');
    text = text.replace(/(?<![a-zA-Z])\\int(?![a-zA-Z])/g,       '\u222b');
    text = text.replace(/(?<![a-zA-Z])\\partial(?![a-zA-Z])/g,   '\u2202');
    text = text.replace(/(?<![a-zA-Z])\\nabla(?![a-zA-Z])/g,     '\u2207');
    text = text.replace(/(?<![a-zA-Z])\\to(?![a-zA-Z])/g,        '\u2192');
    text = text.replace(/(?<![a-zA-Z])\\notin(?![a-zA-Z])/g,     '\u2209');
    text = text.replace(/(?<![a-zA-Z])\\in(?![a-zA-Z])/g,        '\u2208');
    text = text.replace(/(?<![a-zA-Z])\\subseteq(?![a-zA-Z])/g,  '\u2286');
    text = text.replace(/(?<![a-zA-Z])\\subset(?![a-zA-Z])/g,    '\u2282');
    text = text.replace(/(?<![a-zA-Z])\\supseteq(?![a-zA-Z])/g,  '\u2287');
    text = text.replace(/(?<![a-zA-Z])\\supset(?![a-zA-Z])/g,    '\u2283');
    text = text.replace(/(?<![a-zA-Z])\\cup(?![a-zA-Z])/g,       '\u222a');
    text = text.replace(/(?<![a-zA-Z])\\cap(?![a-zA-Z])/g,       '\u2229');
    text = text.replace(/(?<![a-zA-Z])\\equiv(?![a-zA-Z])/g,     '\u2261');
    text = text.replace(/(?<![a-zA-Z])\\hbar(?![a-zA-Z])/g,      '\u210f');

    // Boxed result and decorators (strip command, keep content with combining mark)
    text = text.replace(/(?<![a-zA-Z])\\boxed\{([^}]*)\}/g,      '[$1]');
    text = text.replace(/(?<![a-zA-Z])\\widehat\{([^}]*)\}/g,    '$1\u0302');
    text = text.replace(/(?<![a-zA-Z])\\hat\{([^}]*)\}/g,        '$1\u0302');
    text = text.replace(/(?<![a-zA-Z])\\widetilde\{([^}]*)\}/g,  '$1\u0303');
    text = text.replace(/(?<![a-zA-Z])\\tilde\{([^}]*)\}/g,      '$1\u0303');
    text = text.replace(/(?<![a-zA-Z])\\vec\{([^}]*)\}/g,        '$1\u20d7');
    text = text.replace(/(?<![a-zA-Z])\\overline\{([^}]*)\}/g,   '$1\u0305');
    text = text.replace(/(?<![a-zA-Z])\\bar\{([^}]*)\}/g,        '$1\u0305');
    text = text.replace(/(?<![a-zA-Z])\\ddot\{([^}]*)\}/g,       '$1\u0308');
    text = text.replace(/(?<![a-zA-Z])\\dot\{([^}]*)\}/g,        '$1\u0307');
    text = text.replace(/(?<![a-zA-Z])\\underline\{([^}]*)\}/g,  '$1');
    // --------------------------------------------------------------------

    const images = [];
    text = text.replace(/!\[([^\]]*)\]\((https?:\/\/[^\)]+)\)/g, (m, alt, url) => { const i = images.length; images.push({ alt, url }); return `@@IMAGE_${i}@@`; });
    text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\)]+)\)/g, (m, label, url) => { const i = links.length; links.push({ label, url }); return `@@LINK_${i}@@`; });
    text = text.replace(/(?<!\]\()(?<!")https?:\/\/[^\s<>"]+/g, (url) => {
        let trailing = url.match(/[.,;:!?'"]+$/)?.[0] || '';
        let cleanUrl = url.slice(0, url.length - trailing.length);
        const openCount  = (cleanUrl.match(/\(/g) || []).length;
        const closeCount = (cleanUrl.match(/\)/g) || []).length;
        if (closeCount > openCount) {
            const extra = closeCount - openCount;
            cleanUrl = cleanUrl.slice(0, cleanUrl.length - extra);
            trailing = ')'.repeat(extra) + trailing;
        }
        if (cleanUrl.endsWith(']') && !cleanUrl.includes('[')) {
            trailing = ']' + trailing;
            cleanUrl = cleanUrl.slice(0, -1);
        }
        const i = links.length; links.push({ label: cleanUrl, url: cleanUrl }); return `@@LINK_${i}@@${trailing}`;
    });
    text = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    text = text.replace(/^######\s+(.+)$/gm, "<h6>$1</h6>").replace(/^#####\s+(.+)$/gm, "<h5>$1</h5>").replace(/^####\s+(.+)$/gm, "<h4>$1</h4>").replace(/^###\s+(.+)$/gm, "<h3>$1</h3>").replace(/^##\s+(.+)$/gm, "<h2>$1</h2>").replace(/^#\s+(.+)$/gm, "<h1>$1</h1>");
    text = text.replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>").replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/\*(.+?)\*/g, "<em>$1</em>").replace(/~~(.+?)~~/g, "<del>$1</del>");
    // Task lists (before bullet rule so - [x] isn't consumed as a plain bullet)
    text = text.replace(/^- \[x\]\s+(.+)$/gim, '&#9745; $1');
    text = text.replace(/^- \[ \]\s+(.+)$/gm,  '&#9744; $1');
    // Ordered lists — group consecutive numbered lines into <ol>
    text = text.replace(/((?:^\d+\. .+\n?)+)/gm, (block) => {
        const items = block.trim().split('\n').filter(l => /^\d+\./.test(l.trim())).map(l => `<li>${l.replace(/^\d+\.\s+/, '').trim()}</li>`).join('');
        return `<ol>${items}</ol>`;
    });
    text = text.replace(/^>\s+(.+)$/gm, "<blockquote>$1</blockquote>").replace(/^([-*_]){3,}$/gm, "<hr>").replace(/^- /gm, "&bull; ").replace(/^\* /gm, "&bull; ");
    text = text.replace(/^(\|.+\|)\s*\n(\|[-:\s|]+\|)\s*\n((?:\|.*\|\s*\n?)*)/gm, (match, header, divider, rows) => {
        const makeRow = row => "<tr>" + row.trim().slice(1, -1).split("|").map(cell => `<td>${cell.trim()}</td>`).join("") + "</tr>";
        return `<table><thead>${makeRow(header).replace(/<td>/g, "<th>").replace(/<\/td>/g, "</th>")}</thead><tbody>${rows.trim().split("\n").filter(r => r.trim().startsWith("|")).map(makeRow).join("")}</tbody></table>`;
    });
    text = text.replace(/\n\s*\n/g, "<br><br>").replace(/\n/g, "<br>").replace(/(<\/h[1-6]>)(<br>){2}/g, "$1<br>");
    const processMath = (math) => {
        math = math.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        math = math.replace(/\\text\{([^}]+)\}/g, '$1');
        math = math.replace(/\text\{([^}]+)\}/g, '$1');
        math = math.replace(/\\boxed\{([^}]+)\}/g, '[$1]');
        math = math.replace(/\\\\/g, '<br>');
        math = math.replace(/(?<![a-zA-Z])\\sum(?![a-zA-Z])/g,      '∑');
        math = math.replace(/(?<![a-zA-Z])\\prod(?![a-zA-Z])/g,     '∏');
        math = math.replace(/(?<![a-zA-Z])\\iiint(?![a-zA-Z])/g,    '∭');
        math = math.replace(/(?<![a-zA-Z])\\iint(?![a-zA-Z])/g,     '∬');
        math = math.replace(/(?<![a-zA-Z])\\oint(?![a-zA-Z])/g,     '∮');
        math = math.replace(/(?<![a-zA-Z])\\int(?![a-zA-Z])/g,      '∫');
        math = math.replace(/(?<![a-zA-Z])\\partial(?![a-zA-Z])/g,  '∂');
        math = math.replace(/(?<![a-zA-Z])\\nabla(?![a-zA-Z])/g,    '∇');
        math = math.replace(/(?<![a-zA-Z])\\to(?![a-zA-Z])/g,       '→');
        math = math.replace(/(?<![a-zA-Z])\\notin(?![a-zA-Z])/g,    '∉');
        math = math.replace(/(?<![a-zA-Z])\\in(?![a-zA-Z])/g,       '∈');
        math = math.replace(/(?<![a-zA-Z])\\subseteq(?![a-zA-Z])/g, '⊆');
        math = math.replace(/(?<![a-zA-Z])\\subset(?![a-zA-Z])/g,   '⊂');
        math = math.replace(/(?<![a-zA-Z])\\supseteq(?![a-zA-Z])/g, '⊇');
        math = math.replace(/(?<![a-zA-Z])\\supset(?![a-zA-Z])/g,   '⊃');
        math = math.replace(/(?<![a-zA-Z])\\cup(?![a-zA-Z])/g,      '∪');
        math = math.replace(/(?<![a-zA-Z])\\cap(?![a-zA-Z])/g,      '∩');
        math = math.replace(/(?<![a-zA-Z])\\equiv(?![a-zA-Z])/g,    '≡');
        math = math.replace(/(?<![a-zA-Z])\\hbar(?![a-zA-Z])/g,     'ℏ');
        math = math.replace(/(?<![a-zA-Z])\\widehat\{([^}]*)\}/g,   '$1̂');
        math = math.replace(/(?<![a-zA-Z])\\hat\{([^}]*)\}/g,       '$1̂');
        math = math.replace(/(?<![a-zA-Z])\\widetilde\{([^}]*)\}/g, '$1̃');
        math = math.replace(/(?<![a-zA-Z])\\tilde\{([^}]*)\}/g,     '$1̃');
        math = math.replace(/(?<![a-zA-Z])\\vec\{([^}]*)\}/g,       '$1⃗');
        math = math.replace(/(?<![a-zA-Z])\\overline\{([^}]*)\}/g,  '$1̅');
        math = math.replace(/(?<![a-zA-Z])\\bar\{([^}]*)\}/g,       '$1̅');
        math = math.replace(/(?<![a-zA-Z])\\ddot\{([^}]*)\}/g,      '$1̈');
        math = math.replace(/(?<![a-zA-Z])\\dot\{([^}]*)\}/g,       '$1̇');
        math = math.replace(/(?<![a-zA-Z])\\underline\{([^}]*)\}/g, '$1');
        const symbols = { '\u005cpm': '\u00b1', '\u005ctimes': '\u00d7', '\u005cdiv': '\u00f7', '\u005ccdot': '\u00b7', '\u005cneq': '\u2260', '\u005cleq': '\u2264', '\u005cgeq': '\u2265', '\u005capprox': '\u2248', '\u005cinfty': '\u221e', '\u005cpi': '\u03c0', '\u005crightarrow': '\u2192', '\u005cforall': '\u2200', '\u005cexists': '\u2203', '\u005cdegree': '\u00b0', '\u005cphi': '\u03a6' };
        const tabSymbols = { '\times': '\u00d7', '\pi': '\u03c0' };
        Object.entries(symbols).forEach(([t, s]) => { math = math.split(t).join(s); });
        Object.entries(tabSymbols).forEach(([t, s]) => { math = math.split(t).join(s); });
        math = math.replace(/(?<![a-zA-Z])\\omega(?![a-zA-Z])/g, '\u03c9');
        math = math.replace(/(?<![a-zA-Z])\\theta(?![a-zA-Z])/g, '\u03b8');
        math = math.replace(/(?<![a-zA-Z])\\sigma(?![a-zA-Z])/g, '\u03c3');
        math = math.replace(/(?<![a-zA-Z])\\lambda(?![a-zA-Z])/g, '\u03bb');
        math = math.replace(/(?<![a-zA-Z])\\mu(?![a-zA-Z])/g, '\u03bc');
        math = math.replace(/(?<![a-zA-Z])\\alpha(?![a-zA-Z])/g, '\u03b1');
        math = math.replace(/(?<![a-zA-Z])\\beta(?![a-zA-Z])/g, '\u03b2');
        math = math.replace(/(?<![a-zA-Z])\\gamma(?![a-zA-Z])/g, '\u03b3');
        math = math.replace(/\\left\s*\(/g, '(').replace(/\\right\s*\)/g, ')');
        math = math.replace(/\\left\s*\[/g, '[').replace(/\\right\s*\]/g, ']');
        math = math.replace(/\\left\s*\{/g, '{').replace(/\\right\s*\}/g, '}');
        math = math.replace(/(?<![a-zA-Z])\\sqrt\[([^\]]+)\]\{([^}]+)\}/g, '<sup>$1</sup>\u221a($2)').replace(/\\sqrt\{([^}]+)\}/g, '\u221a($1)').replace(/\^\{([^}]+)\}/g, '<sup>$1</sup>').replace(/\^([a-zA-Z0-9+-]+)/g, '<sup>$1</sup>').replace(/_\{([^}]+)\}/g, '<sub>$1</sub>').replace(/_([a-zA-Z0-9+-]+)/g, '<sub>$1</sub>').replace(/\\frac\{([^}]+)\}\{([^}]+)\}/g, '($1)/($2)');
        return `<strong>${math}</strong>`;
    };
    text = text.replace(/@@INLINE_(\d+)@@/g, (m, i) => `<code class="code-inline">${inline[i].replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")}</code>`);
    text = text.replace(/@@CODEBLOCK_(\d+)@@/g, (m, i) => {
        const block = blocks[i], escaped = block.code.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"), id = `cb_${Date.now()}_${i}`;
        return `<div style="position:relative;margin:8px 0;"><button class="copy-btn" data-target="${id}" style="position:absolute;top:6px;right:6px;background:rgba(255,255,255,0.1);border:none;border-radius:${document.documentElement.getAttribute('data-theme') === 'matrix' ? '0' : '4px'};padding:4px 6px;cursor:pointer;color:#aaa;display:flex;align-items:center;z-index:1;"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button><pre><code id="${id}" class="language-${block.lang}">${escaped.trim()}</code></pre></div>`;
    });
    text = text.replace(/@@MATHINLINE_(\d+)@@/g, (m, i) => processMath(mathInline[i]));
    text = text.replace(/@@MATHBLOCK_(\d+)@@/g, (m, i) => processMath(mathBlock[i]));
    text = text.replace(/@@LINK_(\d+)@@/g, (m, i) => `<a href="${escapeHtml(links[i].url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(links[i].label)}</a>`);
    text = text.replace(/@@IMAGE_(\d+)@@/g, (m, i) => {
        const src = images[i].url.replace(/\/thumbnails\/\d+\//, '/thumbnails/original/');
        const radius = document.documentElement.getAttribute('data-theme') === 'matrix' ? '0' : '6px';
        return `<img src="${escapeHtml(src)}" alt="${escapeHtml(images[i].alt)}" style="max-width:100%;height:auto;border-radius:${radius};display:block;margin:4px 0;object-fit:contain;">`;
    });
    return text;
}
