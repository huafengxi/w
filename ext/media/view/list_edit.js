function toggleComment(btn) {
    var t = btn;
    var html = t.innerHTML;
    if (html.startsWith('#')) {
        html = html.slice(1);
    } else {
        html = '#' + html;
    }
    t.innerHTML = html;
}

function liMoveUp(btn) {
    var li = btn.parentNode;
    if (li.previousSibling) {
        li.parentNode.insertBefore(li, li.previousSibling);
    }
}
function liMoveDown(btn) {
    var li = btn.parentNode;
    if (li.nextSibling) {
        li.parentNode.insertBefore(li.nextSibling, li);
    }
}

function liCopy(btn) {
    var li = btn.parentNode;
    li.parentNode.insertBefore(li.cloneNode(li), li);
}

function liMoveLast(btn) {
    var li = btn.parentNode;
    li.parentNode.appendChild(li);
}

function liSort(btn) {
    var li = btn.parentNode;
    li.parentNode.insertBefore(li, li.parentNode.childNodes[cursor]);
    cursor += 1;
}

function createListEdit(ctrl, list) {
    var drag_ctrl;
    function drag(ev) {
        drag_ctrl = ev.target;
        console.log(ev.target.innerHTML);
        ev.dataTransfer.setData("text/html", ev.target.innerHTML);
    }
    function allowDrop(ev) {
        ev.preventDefault();
    }
    function drop(ev) {
        ev.preventDefault();
        var target = ev.currentTarget;
        if (target && target){
            drag_ctrl.remove();
            target.parentElement.insertBefore(drag_ctrl, target);
        }
    }
    function createListItem(label) {
        var li = document.createElement('li');
        li.draggable = 'true';
        li.ondragstart = drag;
        li.ondragover = allowDrop;
        li.ondrop = drop;
        li.innerHTML = '<pre style="display: inline" onclick="listOp(this)">{label}</pre>'.format({label:label});
        return li;
    }
    function is_empty_line(line) { return !/\S/.test(line); }
    function do_append_list_item(label) {
        if (is_empty_line(label)) return;
        ctrl.appendChild(createListItem(label));
    }
    function getLines() {
        var lines = [];
        var children = ctrl.getElementsByTagName('li');
        for(var i = 0; i < children.length; i++){
            lines.push(children[i].innerText);
        }
        return lines;
    }
    list.forEach(do_append_list_item);
    return getLines;
}

function listOp(li) {
    console.log(li);
    var sel = $('listop').value;
    if (sel == 'hide') {
        toggleComment(li);
    } else if (sel == 'up') {
        liMoveUp(li);
    } else if (sel == 'down') {
        liMoveDown(li);
    } else if (sel == 'copy') {
        liCopy(li);
    } else if (sel == 'last') {
        liMoveLast(li);
    } else if (sel == 'sort') {
        liSort(li);
    }
}
