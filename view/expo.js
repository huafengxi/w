function is_empty_line(line) { return !/\S/.test(line); }
function is_comment(line) { return line.startsWith('#'); }
function is_video(line) { return !is_comment(line) && line.match(/.(mp4|webm|ogg)$/i); }
function retryVideo(e) {
    var video = e.target;
    mylog("handle play error", e, video, video.currentTime);
    if (video.error.code == video.error.MEDIA_ERR_NETWORK) {
        var startTime = video.currentTime;
        mylog("retry play", startTime, video.src);
        video.src = video.src;
        video.load();
        video.currentTime = startTime;
        var playPromise = video.play();
        playPromise.catch(error => {
            mylog("video play error occur", error);
        });
    }
}
function create_video(path) {
    var ctrl = document.createElement('div');
    ctrl.innerHTML = '<video preload="metadata" controls muted src="{path}"></video><pre>{basename}</pre>'.format({path: path, basename:basename(path)})
    function getVideo(ctrl) { return ctrl.children[0]; }
    function playNext() {
        var next = ctrl.nextSibling;
        if (next) {
            getVideo(next).play();
        }
    }
    getVideo(ctrl).playbackRate = 1;
    getVideo(ctrl).onended = playNext;
    getVideo(ctrl).addEventListener('error', retryVideo);
    return ctrl;
}

function is_audio(line) { return !is_comment(line) && line.match(/.(mp3|wav)$/i); }
function retryAudio(e) {
    var audio = e.target;
    mylog("handle play error", e, audio, audio.currentTime);
    if (audio.error.code == audio.error.MEDIA_ERR_NETWORK) {
        var startTime = audio.currentTime;
        mylog("retry play", startTime, audio.src);
        audio.src = audio.src;
        audio.load();
        audio.currentTime = startTime;
        var playPromise = audio.play();
        playPromise.catch(error => {
            mylog("audio play error occur", error);
        });
    }
}
function create_audio(path) {
    var ctrl = document.createElement('div');
    ctrl.innerHTML = '<audio preload="metadata" controls muted src="{path}"></audio><pre>{basename}</pre>'.format({path: path, basename:basename(path)})
    function getAudio(ctrl) { return ctrl.children[0]; }
    function playNext() {
        var next = ctrl.nextSibling;
        if (next) {
            getAudio(next).play();
        }
    }
    getAudio(ctrl).playbackRate = 1;
    getAudio(ctrl).onended = playNext;
    getAudio(ctrl).addEventListener('error', retryAudio);
    return ctrl;
}

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
