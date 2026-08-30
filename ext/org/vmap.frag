# org (org2* converters run via ext/shell/rpc/sh.py: the org src is piped in on
# stdin and `bash -c <cmd>` runs the converter; .es scripts on PATH via bin_dirs)
text/org: /w/ext/org/view/org.html
org2md: /w/ext/shell/rpc/sh.py?cmd=org2markdown.es&pipe_src=1
org2reveal: /w/ext/shell/rpc/sh.py?cmd=org2reveal.es&pipe_src=1
# text/org/bot (org2html.py) 路由已移除：org2html 归档至 ~/m/archive，脚本本就不在 bin_dirs PATH 上（py2 失效）
