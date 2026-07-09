var g_share_player;
var g_cur_play_elem;
function createSharePlayer() {
    var ctrl = document.createElement('div');
    ctrl.innerHTML = '<video preload="metadata" controls muted src=""></video>';
    function getVideo(ctrl) { return ctrl.children[0]; }
    function playNext() {
        var next = g_cur_play_elem.nextSibling;
        if (next) {
            playInSharePlayer(next);
        }
    }
    getVideo(ctrl).addEventListener('ended', playNext);
    g_share_player = getVideo(ctrl);
    return ctrl;
}

function playInSharePlayer(elem) {
    g_cur_play_elem = elem;
    g_share_player.src = elem.children[0].src;
    g_share_player.play();
}
