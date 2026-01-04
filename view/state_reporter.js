var g_currentlyPlaying = null;

function onPlay(event) {
    postPlayingState(event.target);
    g_currentlyPlaying = event.target;
}

function onPause(event) {
    postPlayingState(event.target);
    g_currentlyPlaying = null;
}

function onEnded(event) {
    postPlayingState(event.target);
    g_currentlyPlaying = null;
}

function postPlayingState(media_element) {
    var element_to_report = media_element || g_currentlyPlaying;
    if (element_to_report) {
        let state = {
            file: element_to_report.src,
            state: element_to_report.paused ? 'paused' : 'playing',
            time: element_to_report.currentTime,
            report_time: Date.now()
        };
        write_async('/playing-state.json?v=write', JSON.stringify(state));
    }
}

setInterval(postPlayingState, 1000);
