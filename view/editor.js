function getMatch(s, pat, pos) {
  while(m = pat.exec(s)) {
    if (pos >= m.index && pos < m.index + m[0].length) {
      return [m.index, m[0].length];
    }
  }
  return [pos, 0];
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
