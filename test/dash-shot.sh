#!/bin/bash
# 在 nv1 上用 playwright(chromium headless) 截 8080 dash 页，验证浏览器侧渲染（CSS/布局/iframe/widget 表格）。
# 只读：不写 ~/m，产物落 /tmp/dash-shots/。凭据取本机 ~/.auth/passwd（user:pass）。
set -u
OUT=/tmp/dash-shots
rm -rf "$OUT"; mkdir -p "$OUT"
PY=python3
command -v "$PY" >/dev/null || { echo "no python3"; exit 1; }
"$PY" - <<'PYEOF'
import os, sys, json
try:
    from playwright.sync_api import sync_playwright
except Exception as e:
    print("PLAYWRIGHT_IMPORT_FAIL", repr(e)); sys.exit(2)

cred = None
p = os.path.expanduser("~/.auth/passwd")
if os.path.exists(p):
    raw = open(p).read().strip()
    if ":" in raw:
        u, pw = raw.split(":", 1)
        cred = {"username": u, "password": pw}
print("auth:", "yes" if cred else "NO")

BASE = "http://localhost:8080"
pages = [
    ("session",  "/dev/dash/session.md?_v=autorun",  True),
    ("decide",   "/dev/dash/decide.md?_v=autorun",   False),
    ("todo",     "/dev/dash/todo.md?_v=autorun",     False),
    ("itab",     "/dev/dash/dash.itab",              False),
]

with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(args=["--no-sandbox"])
    except Exception as e:
        print("LAUNCH_FAIL", repr(e)); sys.exit(3)
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000},
                              http_credentials=cred) if cred else \
          browser.new_context(viewport={"width": 1440, "height": 1000})
    page = ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.type + ": " + m.text[:200]) if m.type == "error" else None)
    for name, path, expect_iframe in pages:
        url = BASE + path
        info = {"name": name, "url": url}
        try:
            r = page.goto(url, wait_until="load", timeout=45000)
            info["status"] = r.status if r else None
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                info["networkidle"] = "timeout"
            # widget 输出（表格）与 inline iframe 的到场判定
            for sel, key in (("table", "table_count"),):
                try:
                    info[key] = page.locator(sel).count()
                except Exception as e:
                    info[key] = "err:%r" % e
            if expect_iframe:
                try:
                    page.wait_for_selector(".iframe-preview iframe", timeout=20000)
                    info["inline_iframe"] = page.locator(".iframe-preview iframe").count()
                    fr = page.locator(".iframe-preview iframe").first
                    info["iframe_src"] = fr.get_attribute("src")
                    # 表单是否真渲染在 iframe 内（跨源不允许，同源可读）
                    try:
                        info["iframe_title"] = fr.content_frame.title()
                        info["iframe_has_submit"] = fr.content_frame.locator("#btnSubmit").count()
                        info["iframe_name_value"] = fr.content_frame.locator("#fName").input_value()
                        info["iframe_profile_options"] = fr.content_frame.locator("#fProfile option").count()
                    except Exception as e:
                        info["iframe_probe"] = "err:%r" % e
                except Exception as e:
                    info["inline_iframe"] = "MISSING:%r" % e
            # 文档标题（md 视图取第一个 H1）
            try:
                info["h1"] = page.locator("#doc h1").first.inner_text(timeout=3000)
            except Exception:
                info["h1"] = None
            page.wait_for_timeout(2500)
            out = os.path.join("/tmp/dash-shots", name + ".png")
            page.screenshot(path=out, full_page=(name != "itab"))
            info["png"] = out
            info["png_bytes"] = os.path.getsize(out)
            info["scroll_height"] = page.evaluate("document.documentElement.scrollHeight")
        except Exception as e:
            info["error"] = repr(e)
        print("PAGE " + json.dumps(info, ensure_ascii=False))
    print("CONSOLE_ERRORS " + json.dumps(errs[:12], ensure_ascii=False))
    browser.close()
PYEOF
echo "== 产物"; ls -l "$OUT" 2>/dev/null
