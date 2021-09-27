function getBlock(str, caret, sTag, eTag){
    var s = str.lastIndexOf(sTag, caret-1);
    var e = str.indexOf(eTag, caret);
    return str.substring(s==-1? 0: s+sTag.length, e==-1? str.length: e);
}
function filterLines(content, tag) { return map(function(i){ return i.substring(tag.length);}, content.match(RegExp(tag+'.*$', 'gm'))); }
function getCurLine(content, caret) { return getBlock(content, caret, '\n', '\n'); }

function appendButton(pane, text) {
   var but = document.createElement('input');
   pane.appendChild(but);
   but.type = 'button';
   but.value = text;
   return but;
}
function dump2html(arg, ret, err, _ret, _err) {
     var pan = document.createElement('div');
     _ret.prepend(pan);
     var x = appendButton(pan, 'X');
     var h = appendButton(pan, 'H');
     pan.appendChild(document.createTextNode(arg));
     var res = document.createElement('div');
     pan.appendChild(res);
     x.onclick = function() { pan.remove(); } 
     h.onclick = function() { toggleVisible(res); }
     res.innerHTML = preHtml(str(ret));
     _err.innerHTML = exceptionFormat(err);
     return [ret, err];
}
function dumpCall(func, arg, _ret, _err){ if (!arg) return [null, null]; var res = safeCall(func, arg);  return dump2html(arg, res[0], res[1], _ret, _err);}
function mkDumper(func, _ret, _err) {return function(arg) {return dumpCall(func, arg, _ret, _err); }; }

// lish means `line shell'
function safeCall(func, arg) {
    var result = '', exception = '';
    try{
        result = func(arg);
    }catch(e){
        exception = e;
    }
    return [result, exception]
}

function _lish(interp, input) {
    bindHotKey(top, 'ctrl-alt-i', function(e){ return input.focus(); });
    bindHotKey(input, 'enter', function(e){return interp(e.target.value); });
    input.focus();
    return function(expr){
      return interp(input.value=expr? expr.trim(): ''); };
}

function lish(interp, panel) {
    panel.innerHTML = '<input type="text" class="input"/><pre class="error"></pre><div class="output"></div>';
    return _lish(mkDumper(interp, $s(panel,'output'), $s(panel, 'error')), $s(panel, 'input'));
}

// fish means `file shell'
function fishHandle(interp, input){
  var content = input.value;
  var caret = textAreaGetCaret(input);
  return interp(getCurLine(content, caret), content, caret);
}
function _fish(interp, input) {
    bindHotKey(top, 'ctrl-alt-h', function(e){ return toggleVisible(input);});
    bindHotKey(input, 'alt-button0', function(e){return fishHandle(interp, e.target);});
    bindHotKey(input, 'ctrl-enter', function(e){return fishHandle(interp, e.target); });
    return function(expr, full){return interp(expr, full != undefined? input.value=full: input.value);};
}

function fish(interp, panel, text_filter) {
    panel.innerHTML = '<textarea name="content" class="input" rows="12">${content}</textarea><pre class="error"></pre><div class="status">Fish <button type="button" id="fish_go">Go</button> </div><div class="lish"></div>';
    text_filter = text_filter || function(line, content){ return line;};
    var sh = lish(interp, $s(panel, 'lish'));
    bindHotKey($('fish_go'), 'button0', function(e){ e.stopPropagation(); return fishHandle(doCmd, $s(panel, 'input'));});
    bindHotKey($s(panel, 'status'), 'button0', function(e){return toggleVisible($s(panel, 'input'));});
    bindHotKey($s(panel, 'input'), 'alt-wheel', function(e){ e.target.rows += getWheelDelta(e)/40; e.preventDefault();});
    function doCmd(expr, content, caret){
        function reset_error(){ $s(panel, 'error').innerHTML = ''; };
        function error(msg){return $s(panel, 'error').innerHTML += msg;}
        reset_error();
        try{
            var cmd = text_filter(expr, content, caret);
            sh(cmd);
        } catch(e) {
            error(exceptionFormat(e));
            return;
        }
    }
    return _fish(doCmd, $s(panel, 'input'));
}
