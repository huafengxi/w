# org (org2* converters run via ext/shell/rpc/sh.py: the org src is piped in on
# stdin and `bash -c <cmd>` runs the converter; .es scripts on PATH via bin_dirs)
text/org: /w/ext/org/view/org.html
org2md: /w/ext/shell/rpc/sh.py?cmd=org2markdown.es&pipe_src=1
org2reveal: /w/ext/shell/rpc/sh.py?cmd=org2reveal.es&pipe_src=1
text/org/bot: /w/ext/shell/rpc/sh.py?cmd=org2html.py&pipe_src=1
