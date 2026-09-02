/**
 * sessiond 探针扩展（任务 s0f1la）。
 *
 * 由 ext/sessiond/proc.py:_spawn 对所有会话子进程注入 `-e <本文件绝对路径>`。
 * 只注册一个内部斜杠命令 `sessiond-inspect`（命名冲突排查见任务 s0f1la plan.md：
 * 不与 pi 内置命令、已装 npm 包命令、本地/项目扩展命令、skill 命名空间冲突；
 * 本扩展不注册任何工具，工具名冲突面为零）。
 *
 * 行为：`/sessiond-inspect <nonce>` 把该会话当前的系统提示词全文 + 工具清单
 * 转储为侧车文件 ~/m/run/sessiond-inspect/<nonce>.json（临时文件+rename 原子
 * 落盘），由 sessiond 后端 op=inspect 编排（发命令→等回执→读文件→HTTP 返回，
 * 见 ext/sessiond/bridge.py:inspect）。不走 pi 事件流/jsonl，零会话污染。
 *
 * 健壮性：加载失败只进 pi 的 errors 数组不阻塞启动；命令 handler 整体
 * try/catch，异常也写 error payload 到同一侧车文件，绝不拖垮会话。
 * 无事件钩子、无工具、无后台资源，加载面最小。
 */

export default function sessiondProbe(pi: any) {
	const fs = require("fs");
	const os = require("os");
	const path = require("path");

	// 侧车目录：~/m/run 为宿主本地运行时区（不入 git）。
	const DUMP_DIR = path.join(os.homedir(), "m", "run", "sessiond-inspect");

	function atomicWriteJson(file: string, doc: any) {
		fs.mkdirSync(DUMP_DIR, { recursive: true });
		const tmp = file + ".tmp";
		fs.writeFileSync(tmp, JSON.stringify(doc));
		fs.renameSync(tmp, file);
	}

	pi.registerCommand("sessiond-inspect", {
		description: "sessiond probe: dump system prompt + tool list for op=inspect (internal)",
		handler: async (args: string, ctx: any) => {
			const nonce = String(args || "").trim();
			if (!nonce || !/^[A-Za-z0-9_-]+$/.test(nonce)) return; // 无/非法 nonce：静默 no-op
			const file = path.join(DUMP_DIR, nonce + ".json");
			try {
				const tools = (pi.getAllTools() || []).map((t: any) => ({
					name: t && t.name,
					description: t && typeof t.description === "string" ? t.description : "",
					sourceInfo: t && t.sourceInfo ? t.sourceInfo : null,
				}));
				atomicWriteJson(file, {
					ok: true,
					generatedAt: new Date().toISOString(),
					sessionFile: process.env.SESSIOND_SESSION_FILE || null,
					cwd: process.cwd(),
					toolCount: tools.length,
					tools: tools,
					systemPrompt: ctx.getSystemPrompt(),
				});
			} catch (e: any) {
				try {
					atomicWriteJson(file, {
						ok: false,
						generatedAt: new Date().toISOString(),
						error: String((e && e.message) || e),
					});
				} catch (_e2) { /* 侧车目录都写不了：后端按超时报错，不再抛 */ }
			}
		},
	});
}
