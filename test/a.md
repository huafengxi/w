# inline command demo

#!cmd-interp python3 demo_interp.py

Each `${name}` below renders as three things:

1. a **run** button, to trigger the command.
2. a **log** console (hideable), streaming the command's stderr.
3. a **result** pane, showing the command's stdout rendered as markdown.

The `#!cmd-interp` line above names the interpreter. Clicking a button sends
`/w/ext/shell/rpc/cmd.py?interp=<interp>&path=<this file>&cmd=<name>`, and the server runs
`<interp> <path> <name>`. Here the interp reads this file, finds the matching
` ```cmd <name> ` block, and runs it with bash.

## demo: system info

```cmd sysinfo
echo "collecting system info..." >&2
sleep 1
echo "### system info"
echo ""
echo "| key | value |"
echo "| --- | --- |"
echo "| host | $(hostname) |"
echo "| date | $(date) |"
echo "| kernel | $(uname -r) |"
```

run it: ${sysinfo}

## demo: streaming log

The stderr lines below appear in the log console one per second while the
command runs; the result pane fills in when it finishes.

```cmd count
for i in 1 2 3; do
  echo "step $i of 3 ..." >&2
  sleep 1
done
echo "**done** counting to 3."
```

run it: ${count}
