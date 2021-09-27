function hex(x) { return "0x" + Number(x).toString(16); }
function now() { return new Date().getTime() * 1000; }
function ts2date(ts) { return new Date(ts/1000); }
function int2ip (ipInt) {  return ( (ipInt>>>24) +'.' + (ipInt>>16 & 255) +'.' + (ipInt>>8 & 255) +'.' + (ipInt & 255) );  }
function ip2int(ip) { return ip.split('.').reduce(function(ipInt, octet) { return (ipInt<<8) + parseInt(octet, 10)}, 0) >>> 0; }
function dirname(path) { return path.match(/.*\//);  }

// algorithmic
function identity(x) { return x; }
function minA(A){ return Math.min.apply(null, A); }
function maxA(A){ return Math.max.apply(null, A); }
function min(){ return minA(array(arguments)); }
function max(){ return maxA(array(arguments)); }
function range(n){ var res = []; for(var i = 0; i < n; i++)res.push(i); return res; }
function len(a){ return a.length;}
function listk(iter) { var res = []; for(var i in iter)res.push(i); return res; }
function listv(iter) { var res = []; for(var i in iter)res.push(iter[i]); return res; }
function filter(f, ls){ var res = []; for(var x in ls) if(f(ls[x]))res.push(ls[x]); return res;}
function map(f, ls) { var res = []; for(var x in ls)res.push(f(ls[x])); return res;}
function array(x) { var res = []; for(var i = 0; i < x.length; i++)res.push(x[i]); return res;}
function zipA(As) { var res = []; for(var i in range(minA(map(len, As)))) res.push(map(function(A){ return A[i];}, As)); return res;}
function zip(){ return zipA(array(arguments));}
function enumerate(iter){ return zip(range(len(iter)), iter);}
function dict(pairs){ var result = {}; for(var i in pairs) result[pairs[i][0]] = pairs[i][1];  return result;}
function dict_filter(f, d){ var res = {}; for(var k in d) if(f(d[k]))res[k] = d[k]; return res;}
function dict_map(f, ls) { var res = []; for(var x in ls)res.push([x, f(ls[x])]); return dict(res);}
function repeat(n, x){ var res = []; for (var i=0; i<n; i++)res.push(x); return res;}
function list_mergeA(As){ return [].concat.apply([], As); }
function list_merge(){ return list_mergeA(array(arguments)); }
function bind(obj, attrs) {for (var k in attrs)obj[k]=attrs[k]; return obj;}
function clone(obj) { return obj == null?null: bind({}, obj);}
function dict_merge(A, B){ var res = clone(A); for(var k in B)res[k] = B[k]; return res;}
// string related
function repr(obj) {return JSON.stringify(obj);}
function str(obj){ return typeof obj == 'string'? obj: repr(obj);}
function sub(str, dict) { return str.replace(/{(\w+)}/g, function(m,k){ return dict[k]; }); }
String.prototype.format = function(dict) { return sub(this, dict); }
String.prototype.seqSub = function(pat, seq) {self=this; return map(function(x) {return typeof(x)=="string"? self.replace(pat, x): x; }, seq); }
function basename(path) {return path.replace(/.*\//, ''); }
function dirname(path) {return path.replace(/\/[^\/]*$/, ''); }
function str2dict(pat, str){
    var rexp = pat.replace(/\(\w+=(.*?)\)/g, '($1)');
    var keys = pat.match(/\((\w+)=.*?\)/g) || [];
    var m = null;
    keys = keys.map(function(i){return i.replace(/\((\w+)=.*?\)/, '$1');});
    m = str.match(rexp);
    return  m? dict(zip(['__self__'].concat(keys), m)): null;
}

//global environment
function mylog() { window.console &&  console.log.apply(window.console, arguments); }
function getUrl() { return window.location.pathname; }
function removeQueryArg(uri, param) { return uri.replace(new RegExp("[&\?]{0}*$|{0}&|[?&]{0}(?=#)".format([param])), ''); }
function getQueryArgs(){ return parseQueryString(window.location.search.substring(1));}
function parseQueryString(query){ return dict(map(function(p) { return p.match(/([^=]+)=(.*)/).slice(1); }, filter(identity, query.split('&'))));}
function encodeQueryString(args){ return map(function(k){ return k + "=" + encodeURIComponent(args[k].toString()); },  listk(args)).join('&');}
function obj2QueryString(obj){return encodeQueryString(dict_map(repr, obj));}
function errorFormat(msg, url, lineno){ return repr([msg, url, lineno]);}
function exceptionFormat(e){ return e? e.toString() + '\n' + str(e.stack): "";}
function onerror(msg, url, lineno) { alert(errorFormat(msg, url, lineno)); return true;}

//window.onerror = onerror;

// DOM element operation
function $(id) {return document.getElementById(id); }
function $n(tag) {return document.createElement(tag); }
function $F(id) {return $(id).value; }
function $c(w, c) { return filter(function (x){ return x.className == c; }, w.childNodes); }
function $s(w, c) { return $c(w, c)[0]; }
function insertAfter(parent, node, referenceNode){ parent.insertBefore(node, referenceNode.nextSibling);}
function hide(w) { w.style.display = 'none';}
function show(w) { w.style.display = 'block'; }
function getStyle(w) { return window.getComputedStyle(w, null); }
function hideAll(list) {
    for(var i = 0; i < list.length; i++) {
        hide(list[i]);
    }
}

function isVisible(w) {return getStyle(w).display != 'none'; }
function toggleVisible(w) { isVisible(w)?hide(w):show(w);}
function setHeight(w) {w.style.height = w.scrollHeight + "px";}
function scrollTo(w, top) {w.scrollTop = top;}
function textAreaGetCaret(textArea) {return textArea.selectionStart;}
function textAreaSetCaret(textArea, caret) {textArea.setSelectionRange(caret, caret);}
function setWindowTitle(title) { window.document.title = title; }

function inIframe () { try { return window.self !== window.top; } catch (e) { return true; } }
function tune_iframe_height(iframe, max_height) { iframe.style.height= min(iframe.contentWindow.document.body.scrollHeight, max_height) + 'px'; }

function selectNode(node){
    var select = window.getSelection();
    var range = document.createRange();
    range.selectNode(node);
    select.removeAllRanges();
    select.addRange(range);
}

function escape_special_char(text) {
  return text.replace(/[&<>]/g, function (matched) { return "&#" + matched.charCodeAt(0) + ';'; });
}
function isHtml(s) {return s && s.match(/^\s*<(.|\n)+>\s*$/) && (s.match(/<.*?>/g) || []).length > 5; }
function preHtml(s) {return isHtml(s)? s: '<pre>' + escape_special_char(s) + '</pre>'; }
//function preHtml(s) {return isHtml(s)? s: '<pre onClick="selectNode(this.firstChild)">' + s + '</pre>'; }
function insertText(el, newText) {
  var start = el.selectionStart;
  var end = el.selectionEnd;
  var text = el.value;
  var before = text.substring(0, start)
  var after  = text.substring(end, text.length)
  el.value = (before + newText + after)
  el.selectionStart = el.selectionEnd = start + newText.length
  el.focus()
}

// hot key/click related
function parseHotKey(key){
    var funcKeys = {backspace:8, tab:9, enter:13, pause:19, escape:27, space:32,
                   pageup:33, pagedown:34, end:35, home:36, left:37, up:38, right:39, down:40,
                   print:44, insert:45, del:46,
                   f1:112, f2:113, f3:114, f4:115, f5:116, f6:117, f7:118, f8:119, f9:120, f10:121, f11:122, f12:123,
                   numlock:144, scrolllock:145,};
    function km(pat){ var m = key.toLowerCase().match(pat); return m? m[0]: null;}
    function get_normal_key(){ var key = km(/\W[a-zA-Z]$/); return key?key[1]:null;}
    return {ctrl:km('ctrl'), alt:km('alt'), shift:km('shift'), button: km(/button[0-9]$/), wheel: km(/wheel$/),
            key: get_normal_key(), func: funcKeys[km(/\w+$/)]};
}

function isHotKey(e, key){
    var k = parseHotKey(key);
    return (!k.ctrl || e.ctrlKey) && (!k.alt || e.altKey) && (!k.shift || e.shiftKey)
        && (!k.key || k.key.toUpperCase() == String.fromCharCode(e.keyCode).toUpperCase())
        && (!k.func || k.func == e.which)
        && (!k.button || parseInt(k.button.substr(-1)) == e.button)
        && (k.key || k.func || k.button || k.wheel);
}

function getWheelDelta(e){ return e.wheelDelta != null? e.wheelDelta: 40*e.detail;}
function hotKeyType(key) { return key.match('wheel$')? 'mousewheel:DOMMouseScroll': key.match('button[0-9]$')? 'click': 'keydown'; }
function bindHotKey(w, key, handler){ map(function(type){ w.addEventListener(type, function(e){ isHotKey(e, key) && handler(e);}, false);}, hotKeyType(key).split(':')); }
function bindHotKeys(w, bindings){ for(var name in bindings)bindHotKey(w, name, bindings[name]); }

function setCookie(name, value){ document.cookie = name + "=" + encodeURIComponent(value) + "; max-age=604800; path=/; domain="; }
function getCookie(name){
    var cookie_string = document.cookie;
    if (!cookie_string)
        return '';
    var cookie_value = cookie_string.match('[\s]*' +  name + '=([^;]*)');
    if (!cookie_value)
        return '';
    return decodeURIComponent(cookie_value[1]);
}

// rpc
function http(url, content){
    var req = new XMLHttpRequest();
    req.open("POST", url, false);
    req.setRequestHeader("Content-type", "application/x-www-form-urlencoded");
    //req.setRequestHeader("Connection", "close");
    req.send(content);
    return req.responseText;
}
function splitByFirstLine(str) { var i = str.indexOf('\n')+1; return [str.substring(0, i), str.substr(i)]; }
function rpcDecode(str){
  if (str.match(/^Exception:/)) {
    throw str;
  }
  return str;
}

function rpc(url, content){ if (!content)content = {}; content['token'] = getCookie('token'); return rpcDecode(http(url? url:getUrl(), encodeQueryString(content))); }
function rpc_link(url, content){ return (url? url:getUrl()) + '?' + encodeQueryString(content); }

function read(url){return rpc(url, {v:'read'} ); }
function write(url, content){ return rpc(url, {v:'write', 'store_content':content}); }


function getMatch(s, pat, pos) {
  while(m = pat.exec(s)) {
    if (pos >= m.index && pos < m.index + m[0].length) {
      return [m.index, m[0].length];
    }
  }
  return [pos, 0];
}

function selectBlock(textarea, event) {
}

function makeEditor(panel, on_save) {
    panel.innerHTML = '<button type="button" id="save_editor_button">save</button><button type="button" id="resize_editor_button">resize</button><button type="button" id="upload_button">upload</button><button type="button" id="orig_link_button">orig link</button><textarea name="content" class="input" style="height:100%">${content}</textarea><pre class="error"></pre>';
    var content = read(getUrl());
    var editor = $s(panel, 'input');
    editor.value = content;
    function set_error(err_msg) {
        $s(panel, "error").innerHTML = err_msg;
    }
    function on_save_no_exception(content) {
        try {
            on_save(content);
        } catch(e) {
            set_error("on save failed\n" + e);
        }
    }
    on_save_no_exception(content);
    function save() {
        set_error("");
        try {
            write(getUrl(), editor.value);
        } catch (e) {
            set_error("write failed\n" + e);
        }
        on_save_no_exception(editor.value);
    }
    function toggle_editor(){
        toggleVisible(panel);
        if (isVisible(panel))
        {
            editor.focus();
        }
    }
    function change_editor_height(){
      var input = $s(panel, "input");
      if (input.style.height == "25%") {
        input.style.height = "100%";
      } else if (input.style.height == "100%") {
        input.style.height = "25%";
      }
    }
    function upload() {
      var name = prompt("enter upload name");
      insertText($s(panel, "input"), "[[" + name + "][" + name + "]]");
      open(name + "?v=fops");
    }
    function jump2orig() {
      window.location.href = removeQueryArg(window.location.href, "v=code");
    }
    function markBlock(w, e) {
      var m = getMatch(w.value, /((#[+]begin(.|\n)*?^#[+]end.*?)|(^:.*$))$/gm, w.selectionStart);
      w.setSelectionRange(m[0], m[0] + m[1]);
    }
    function emptySelection(w) {
      w.getSelection.empty();
    }
    toggle_editor();
    bindHotKey($("save_editor_button"), "button0", function(e){ return save(); });
    bindHotKey($("resize_editor_button"), "button0", function(e){ return change_editor_height(); });
    bindHotKey($("upload_button"), "button0", function(e){ return upload(); });
    bindHotKey($("orig_link_button"), "button0", function(e){ return jump2orig(); });
    bindHotKey(editor, "ctrl-alt-m", function(e){ return markBlock(editor, e); });
    bindHotKey(document, 'ctrl-alt-f', function(e) {return change_editor_height();});
    bindHotKey(document, 'ctrl-alt-e', function(e) {return toggle_editor();});
    bindHotKey(document, 'ctrl-alt-s', function(e) {return save();});
    bindHotKey(document, 'ctrl-alt-i', function(e) {return upload();});
}

function parse_org(orgCode)
{
    var orgParser = new Org.Parser();
    var orgDocument = orgParser.parse(orgCode);
    bind(orgDocument.options, {'toc': 0, 'num': 0});
    return orgDocument;
}

function render_org(orgDocument)
{
    var orgHTMLDocument = orgDocument.convert(Org.ConverterHTML, {
        headerOffset: 1,
        exportFromLineNumber: false,
        suppressSubScriptHandling: false,
        suppressAutoLink: false
    });
    return orgHTMLDocument.toString();
}

function lastPartOfSectionNumber(sectionNum) {
    return sectionNum.replace(/.*?([0-9]+)$/, '$1');
}

function supressSectionNumber(pannel) {
    return map(function(node) {node.innerHTML = lastPartOfSectionNumber(node.innerHTML);}, pannel.getElementsByClassName("section-number"));
}
