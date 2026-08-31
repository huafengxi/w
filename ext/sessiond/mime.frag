# .jsonl 默认按聊天视图渲染（任务 0829-2103-dpe4）：无 ?v= 打开任意 .jsonl
# 即聊天窗（等价 ?v=chat）；?v= 显式参数仍可覆盖（如 ?v=code 看原文）。
.jsonl application/x-sessiond-jsonl
# .agent 默认按聊天视图渲染（任务 kcywpy）：xxx.agent = agent 规格 JSON（host +
# workdir），打开即聊天窗，会话参数从 JSON 读取（解析见 rpc/api.py op=agent）。
.agent application/x-sessiond-agent
