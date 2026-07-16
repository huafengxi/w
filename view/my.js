// path
function dirname(path) { return path.replace(/\/[^\/]*$/, ''); }
function basename(path) { return path.replace(/.*\//, ''); }

// algorithmic
function map(f, ls) { const res = []; for (const x in ls) res.push(f(ls[x])); return res; }
function bind(obj, attrs) { return Object.assign(obj, attrs); }
function dict_merge(A, B) { return Object.assign({}, A, B); }

// string
function str(obj) { return typeof obj === 'string' ? obj : JSON.stringify(obj); }
String.prototype.format = function(dict) {
    return this.replace(/{(\w+)}/g, (m, k) => dict[k]);
};

// global environment
function mylog(...args) { console.log(...args); }
function getUrl() { return window.location.pathname; }
function removeQueryArg(uri, param) {
    return uri.replace(new RegExp("[&\\?]{0}*$|{0}&|[?&]{0}(?=#)".format([param])), '');
}
function getQueryArgs() {
    const query = window.location.search.substring(1);
    const result = {};
    for (const p of query.split('&').filter(Boolean)) {
        const m = p.match(/([^=]+)=(.*)/);
        if (m) result[m[1]] = m[2];
    }
    return result;
}
function encodeQueryString(args) {
    return Object.keys(args)
        .map(k => k + "=" + encodeURIComponent(args[k].toString()))
        .join('&');
}
function exceptionFormat(e) { return e ? e.toString() + '\n' + str(e.stack) : ""; }

// DOM element operation
function $(id) { return document.getElementById(id); }
function $n(tag) { return document.createElement(tag); }
function $s(w, c) { return Array.from(w.childNodes).find(x => x.className === c); }
function hide(w) { w.style.display = 'none'; }
function show(w) { w.style.display = 'block'; }
function hideAll(list) { for (const w of list) hide(w); }
function isVisible(w) { return window.getComputedStyle(w, null).display !== 'none'; }
function toggleVisible(w) { isVisible(w) ? hide(w) : show(w); }
function textAreaGetCaret(textArea) { return textArea.selectionStart; }
function setWindowTitle(title) { window.document.title = title; }
function inIframe() { try { return window.self !== window.top; } catch (e) { return true; } }

function preHtml(s) {
    const looksLikeHtml = s && s.match(/^\s*<(.|\n)+>\s*$/) && (s.match(/<.*?>/g) || []).length > 5;
    if (looksLikeHtml) return s;
    const escaped = s.replace(/[&<>]/g, m => "&#" + m.charCodeAt(0) + ';');
    return '<pre>' + escaped + '</pre>';
}

function insertText(el, newText) {
    const start = el.selectionStart;
    const end = el.selectionEnd;
    const text = el.value;
    el.value = text.substring(0, start) + newText + text.substring(end);
    el.selectionStart = el.selectionEnd = start + newText.length;
    el.focus();
}

// hot key / click
function getWheelDelta(e) { return e.wheelDelta != null ? e.wheelDelta : 40 * e.detail; }

function bindHotKey(w, key, handler) {
    const funcKeys = {
        backspace:8, tab:9, enter:13, pause:19, escape:27, space:32,
        pageup:33, pagedown:34, end:35, home:36, left:37, up:38, right:39, down:40,
        print:44, insert:45, del:46,
        f1:112, f2:113, f3:114, f4:115, f5:116, f6:117, f7:118, f8:119, f9:120, f10:121, f11:122, f12:123,
        numlock:144, scrolllock:145,
    };
    const km = pat => { const m = key.toLowerCase().match(pat); return m ? m[0] : null; };
    const normalKeyMatch = km(/\W[a-zA-Z]$/);
    const k = {
        ctrl: km('ctrl'), alt: km('alt'), shift: km('shift'),
        button: km(/button[0-9]$/), wheel: km(/wheel$/),
        key: normalKeyMatch ? normalKeyMatch[1] : null,
        func: funcKeys[km(/\w+$/)],
    };
    const matches = e =>
        (!k.ctrl || e.ctrlKey) && (!k.alt || e.altKey) && (!k.shift || e.shiftKey)
        && (!k.key || k.key.toUpperCase() === String.fromCharCode(e.keyCode).toUpperCase())
        && (!k.func || k.func === e.which)
        && (!k.button || parseInt(k.button.substr(-1)) === e.button)
        && (k.key || k.func || k.button || k.wheel);
    const types = key.match('wheel$') ? 'mousewheel:DOMMouseScroll'
                : key.match('button[0-9]$') ? 'click'
                : 'keydown';
    for (const t of types.split(':')) {
        w.addEventListener(t, e => { if (matches(e)) handler(e); }, false);
    }
}

// cookies
function setCookie(name, value) {
    document.cookie = name + "=" + encodeURIComponent(value) + "; max-age=604800; path=/; domain=";
}
function _token() {
    const m = document.cookie.match(/(?:^|;\s*)token=([^;]*)/);
    return m ? decodeURIComponent(m[1]) : '';
}

// rpc
function http(url, content) {
    const req = new XMLHttpRequest();
    req.open("POST", url, false);
    req.setRequestHeader("Content-type", "application/x-www-form-urlencoded");
    req.send(content);
    return req.responseText;
}

function rpc(url, content) {
    if (!content) content = {};
    content['token'] = _token();
    const res = http(url || getUrl(), encodeQueryString(content));
    if (res.match(/^Exception:/)) throw res;
    return res;
}

// Streaming POST: on_chunk(text) is called for each decoded chunk as it arrives.
function rpc_stream(url, content, on_chunk, on_done, on_error) {
    if (!content) content = {};
    content['token'] = _token();
    fetch(url || getUrl(), {
        method: "POST",
        headers: {"Content-type": "application/x-www-form-urlencoded"},
        body: encodeQueryString(content),
    }).then(resp => {
        const reader = resp.body.getReader();
        const decoder = new TextDecoder('utf-8');
        const pump = () => reader.read().then(r => {
            if (r.done) {
                const tail = decoder.decode();
                if (tail && on_chunk) on_chunk(tail);
                if (on_done) on_done();
                return;
            }
            const text = decoder.decode(r.value, {stream: true});
            if (text && on_chunk) on_chunk(text);
            return pump();
        });
        return pump();
    }).catch(e => { if (on_error) on_error(e); });
}

function write_async(url, content, on_load) {
    const args = {v: 'write', store_content: content, token: _token()};
    const req = new XMLHttpRequest();
    req.open("POST", url, true);
    req.setRequestHeader("Content-type", "application/x-www-form-urlencoded");
    req.onload = () => { if (on_load) on_load(req.responseText); };
    req.send(encodeQueryString(args));
}

function read(url) { return rpc(url, {v: 'read'}); }
function write(url, content) { return rpc(url, {v: 'write', store_content: content}); }
