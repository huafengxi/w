/**
 * sessiond's pi-side adapter, explicitly loaded with -e by ordinary Client and
 * the agentd adapter. Runs inside pi; no Web server or P transport logic here.
 *
 * sessiond-inspect returns the current system prompt and registered tool list.
 * sessiond-chat queries live chat state or regenerates on the same history tree.
 * Nonce-addressed sidecar results are read/deleted by Bridge; queries do not add
 * model messages. Regeneration deliberately appends a new branch via public APIs.
 * No model-callable tools, background processes, sockets or timers are registered.
 */

import { VERSION } from "@earendil-works/pi-coding-agent";

export default function sessiondProbe(pi: any) {
	const fs = require("fs");
	const path = require("path");

	// 侧车目录使用调用方工作区的运行时区。
	const workspace = process.env.W_WORKSPACE;
	if (!workspace || !path.isAbsolute(workspace)) throw new Error('W_WORKSPACE is required');
	const DUMP_DIR = path.join(workspace, "run", "sessiond-inspect");

	function atomicWriteJson(file: string, doc: any) {
		fs.mkdirSync(DUMP_DIR, { recursive: true });
		const tmp = file + ".tmp";
		fs.writeFileSync(tmp, JSON.stringify(doc));
		fs.renameSync(tmp, file);
	}

	// Same-file public tree navigation keeps sessiond's registered path stable.
	let compacting = false;
	let compactError = false;
	let editing = false;
	pi.on("session_before_compact", () => { compacting = true; compactError = false; });
	pi.on("session_compact", () => { compacting = false; });
	pi.on("session_compact_failed", (e: any) => { compacting = false; compactError = !e.aborted; });
	pi.on("agent_start", () => { compactError = false; });
	function state(ctx: any) {
		const branch = ctx.sessionManager.getBranch();
		const messages = branch.filter((e: any) => e.type === "message");
		const last = messages[messages.length - 1]?.message;
		const assistant = messages.findLast((e: any) => e.message.role === "assistant")?.message;
		const busy = !ctx.isIdle() || ctx.hasPendingMessages() || compacting;
		const outcome = compactError || assistant?.stopReason === "error" ? "failed"
			: assistant?.stopReason === "aborted" || last?.role === "user" ? "stopped"
			: assistant ? "success" : "ready";
		return {state: busy ? "busy" : outcome, busy, piVersion: VERSION, piEntrypoint: process.argv[1],
			sessionFile: ctx.sessionManager.getSessionFile(), leafId: ctx.sessionManager.getLeafId()};
	}
	pi.registerCommand("sessiond-chat", {
		description: "sessiond internal chat state and same-file regeneration",
		handler: async (args: string, ctx: any) => {
			let request: any;
			try { request = JSON.parse(Buffer.from(args.trim(), "base64").toString()); }
			catch (_) { return; }
			if (!/^[a-f0-9]{32}$/.test(request.nonce || "")) return;
			const file = path.join(DUMP_DIR, request.nonce + ".json");
			try {
				if (request.action === "state") {
					atomicWriteJson(file, {ok: true, ...state(ctx)}); return;
				}
				if (request.action !== "regenerate") throw new Error("Unknown chat action");
				if (editing || state(ctx).busy) throw new Error("Agent busy; stop or wait before historical regeneration");
				const sm = ctx.sessionManager;
				const originalFile = sm.getSessionFile();
				if (!originalFile || path.resolve(originalFile) !== path.resolve(process.env.SESSIOND_SESSION_FILE || ""))
					throw new Error("Session persistence path differs from sessiond registration");
				const entry = sm.getEntry(request.entryId);
				if (!entry || entry.type !== "message" || entry.message.role !== "user"
					|| !sm.getBranch().some((e: any) => e.id === entry.id))
					throw new Error("User entry is not on the current branch; refresh history");
				if (typeof request.message !== "string" || !Array.isArray(request.images || []))
					throw new Error("Invalid historical message");
				const content: any[] = [];
				if (request.message) content.push({type: "text", text: request.message});
				for (const image of request.images || []) {
					if (image.type !== "image" || typeof image.data !== "string" || typeof image.mimeType !== "string")
						throw new Error("Invalid image");
					content.push({type: "image", data: image.data, mimeType: image.mimeType});
				}
				if (!content.length) throw new Error("Empty historical message");
				// navigateTree(currentLeaf) is a no-op in pi. A public custom entry
				// supplies a non-context breadcrumb for an incomplete user-only leaf.
				if (sm.getLeafId() === entry.id) pi.appendEntry("sessiond-regeneration", {entryId: entry.id});
				editing = true;
				try {
					const result = await ctx.navigateTree(entry.id, {summarize: false});
					if (result.cancelled) throw new Error("Tree navigation cancelled by an extension");
					if (sm.getSessionFile() !== originalFile || sm.getLeafId() !== entry.parentId)
						throw new Error("Unexpected tree navigation result; message not submitted");
					// Public API starts a new user turn; generation failures are normal
					// streamed events, not a claim of completed generation here.
					pi.sendUserMessage(content);
					atomicWriteJson(file, {ok: true, submitted: true, sessionFile: originalFile,
						parentId: entry.parentId, previousEntryId: entry.id});
				} finally { editing = false; }
			} catch (e: any) {
				atomicWriteJson(file, {ok: false, error: String(e?.message || e)});
			}
		},
	});

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
