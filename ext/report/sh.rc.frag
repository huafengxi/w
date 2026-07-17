# Bridge markdown.html inline commands to the report tree's ido: turn the
# doc's on-disk path ($src, set by sh.py) into ido dot-form and dispatch
#   $ido_root/ido/ido.py <a.b.c>.<sub> <opts...>
# sh.py's stream=2 framing tags stdout ('1' -> result pane) and stderr
# ('2' -> log console), so the dispatch line below is written to stderr.
ido_report_cmd() {
    # sh.py passes the whole cmd as one shlex-quoted arg ($1); re-split it
    # honoring quotes/globs so $1 is the sub and $2.. are its args.
    eval "set -- $1"
    local sub=$1; shift
    # .../report/prod_g/v1/release-regression.md -> prod_g.v1: drop the
    # trailing .md filename, then keep the dir segments after the last
    # 'report', joined with '.'.
    local -a parts=() kept=()
    local p i idx=-1 IFS=/
    for p in $src; do [ -n "$p" ] && parts+=("$p"); done
    unset IFS
    if [ ${#parts[@]} -gt 0 ]; then
        case ${parts[${#parts[@]}-1]} in *.md) unset 'parts[${#parts[@]}-1]';; esac
    fi
    for i in "${!parts[@]}"; do [ "${parts[i]}" = report ] && idx=$i; done
    for i in "${!parts[@]}"; do
        if [ "$idx" -lt 0 ] || [ "$i" -gt "$idx" ]; then kept+=("${parts[i]}"); fi
    done
    local dotted IFS=.; dotted="${kept[*]}"; unset IFS
    local target=${dotted:+$dotted.}$sub
    if [ -z "${ido_root:-}" ]; then
        echo "ido_report_cmd: ido_root not set; cannot dispatch '$target'" >&2
        return 1
    fi
    local root=$ido_root
    echo "dispatching '$target' $* via $root/ido/ido.py" >&2
    # ido's markdown result goes to stdout (tag 1 -> result pane); force it
    # unbuffered so it streams live instead of flushing only at exit, matching
    # the already-unbuffered stderr (tag 2 -> log console).
    ( cd "$root" && PYTHONUNBUFFERED=1 "$root/ido/ido.py" "$target" "$@" )
}
