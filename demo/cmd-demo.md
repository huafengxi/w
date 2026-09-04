# cmd widget 演示

本页演示 markdown 视图 cmd widget 的裸 shell 缺省语义：`${...}` 写在正文中（代码块外）即生成可运行的 widget，点 run 执行命令。

当前时间（点 run 即显示）：

${date}

磁盘使用情况（stdout 按 markdown 渲染）：

${df -h | head -3}

系统负载：

${uptime}
