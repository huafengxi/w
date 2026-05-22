function is_empty_line(line) { return !/\S/.test(line); }
function is_comment(line) { return line.startsWith('#'); }
function is_video(line) { return !is_comment(line) && line.match(/.(mp4|webm|ogg)$/i); }
function is_audio(line) { return !is_comment(line) && line.match(/.(mp3|wav|flac)$/i); }

function retryMedia(e) {
    var el = e.target;
    mylog("handle play error", e, el, el.currentTime);
    if (el.error.code == el.error.MEDIA_ERR_NETWORK) {
        var startTime = el.currentTime;
        mylog("retry play", startTime, el.src);
        el.src = el.src;
        el.load();
        el.currentTime = startTime;
        el.play().catch(error => {
            mylog("media play error occur", error);
        });
    }
}

function createMediaCtrl(tag, path) {
    var ctrl = document.createElement('div');
    ctrl.innerHTML = ('<' + tag + ' preload="metadata" controls muted src="{path}"></' + tag + '><pre>{basename}</pre>')
        .format({path: path, basename: basename(path)});
    function getMedia() { return ctrl.children[0]; }
    function playNext() {
        var next = ctrl.nextSibling;
        if (next) { next.children[0].play(); }
    }
    function playInShare(event) { playInSharePlayer(event.target.parentNode); }
    ctrl.children[1].addEventListener('click', playInShare);
    var m = getMedia();
    m.addEventListener('ended', onEnded);
    m.addEventListener('ended', playNext);
    m.addEventListener('error', retryMedia);
    m.addEventListener('play', onPlay);
    m.addEventListener('pause', onPause);
    return ctrl;
}

function create_video(path) { return createMediaCtrl('video', path); }
function create_audio(path) { return createMediaCtrl('audio', path); }

function is_text(line) { return line.match(/.(txt|text)$/i); }
function toggleTextVisible(ctrl, path) {
    var parent = ctrl.parentNode;
    if (parent.children.length < 2) {
        var textCtrl = document.createElement('pre');
        textCtrl.innerHTML = read(path);
        parent.appendChild(textCtrl);
    } else {
        toggleVisible(parent.children[1]);
    }
}
function create_text(path) {
    var ctrl = document.createElement('div');
    ctrl.innerHTML = '<h3>{path}</h3>'.format({path: path});
    function toggle(ev) {
        return toggleTextVisible(ev.target, path);
    }
    ctrl.children[0].addEventListener('click', toggle, false);
    return ctrl;
}

function is_img(line) { return line.match(/.(jpg|jpeg|png|gif)$/i); }
function create_img(path) {
    var ctrl = document.createElement('div');
    ctrl.innerHTML = '<img src="{path}">'.format({path: path})
    return ctrl;
}

function is_playlist(path) { return path.match(/.(plist)$/); }
function create_plist(path) {
    var ctrl = document.createElement('li');
    ctrl.innerHTML = '<a target="_blank" href="{path}?v=plist">{path}</a>'.format({path: path});
    return ctrl;
}
function appendExpo(list_ctrl) {
    function do_append_expo(path) {
        if (is_img(path)) {
            list_ctrl.appendChild(create_img(path));
        } else if (is_text(path)) {
            list_ctrl.appendChild(create_text(path));
        } else if (is_audio(path)) {
            list_ctrl.appendChild(create_audio(path));
        } else if (is_video(path)) {
            list_ctrl.appendChild(create_video(path));
        } else if (is_playlist(path)) {
            list_ctrl.appendChild(create_plist(path));
        }
    }
    return do_append_expo;
}
