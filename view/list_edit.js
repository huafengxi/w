function toggleComment(t) {
    var html = t.innerHTML;
    if (html.startsWith('#')) {
        html = html.slice(1);
    } else {
        html = '#' + html;
    }
    t.innerHTML = html;
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
        li.innerHTML = '<pre onclick="toggleComment(this)">{label}</pre>'.format({label:label});
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
            lines.push(children[i].childNodes[0].innerText);
        }
        return lines;
    }
    list.forEach(do_append_list_item);
    return getLines;
}
