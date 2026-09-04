# document edit server

This is a document edit server. see [design.md](design.md).
once deployed, you can creat file with special extensions for different purpose.

> **shell 路由**：GET 已映射视图的扩展名路径（如 `.md`）即使文件不存在也返 200 + HTML 外壳（内容真实性由客户端取内容时才见分晓）；其余不存在路径返 404（`w/view/404.html`）。页面内容里的链接必须写**绝对路径**，相对链接会被当作页面路径吃掉；`.md` 链接无需 `?v=text/md` 后缀（服务端按扩展名推断视图），仅 `.py` 等非 md 文件渲 md 视图时才带 `?v=...`；md 视图链接带 `_v=inline`（如 `?_v=inline`）表示渲染后默认在页内 iframe 就地展开（点击收起/再展开，可多个同开；`_v=` 是客户端私有参数命名空间——下划线前缀约定，服务端不消费，构造内嵌 iframe 前剥离）。

> **运维要点**：8080 basic auth 凭证 = `~/.auth/passwd`（`env/pi-web.auth` 是 8192 的）；`w/stores/cmd_store.py` 不得删。

## start service

```
cd $webroot # change to your webroot
git clone git@github.com:huafengxi/w.git w
log=debug w/core/server.py 8080
```

The server is split into a feature-free `core/` plus per-feature `ext/<feature>/`
extensions. vmap, mime, bash rc, and `PATH` bin dirs are picked up by convention
from `ext/<name>/{vmap,mime,sh.rc}.frag` and `ext/<name>/bin/` (composed by
`w/vmap`, `w/mime`, `ext/shell/sh.rc`, and `core/server.py:set_path`).

## config ssl/basic auth

```
echo 'user:passwd' > ~/.auth/passwd
cp cert.pem privkey.pem  ~/.auth/
```

## download optional deps for extend features

```
cd $webroot
git clone git@github.com:huafengxi/tsql.git tsql # query text using sql
git clone git@github.com:huafengxi/bin-mirror.git deps2 # revealjs
```

## iframe show many doc
- [/w/demo/list.iframe?v=iframe](/w/demo/list.iframe?v=iframe)

## .org file

- [/w/demo/a.org](/w/demo/a.org), ctrl-alt-e to edit, ctrl-alt-s to save and refresh.
- [?v=org2md](?v=org2md) convert org to markdown
- [?v=org2reveal](?v=org2reveal) for presentation, need place revealjs in `/deps` dir.

## shell

- [/w/demo/host.stat](/w/demo/host.stat) call remote shell script, [/w/demo/host.stat?v=read](/w/demo/host.stat?v=read) for raw file.
- [/w/demo/interp.py?cmd=hello](/w/demo/interp.py?cmd=hello) call remote function `interp()` defined in python file.
- [/w/demo/interp.py?v=q](/w/demo/interp.py?v=q) interactive `interp()` defined in python file.
- [/w/demo/demo.ish](/w/demo/demo.ish) ctrl+click to execute cmd.
- [/w/demo/cmd-demo.md](/w/demo/cmd-demo.md) markdown `${...}` inline cmd widget: default raw server-side shell, output streams into the page (syntax & semantics: ext/markdown/README.md).

## table

- [/w/demo/a.tab](/w/demo/a.tab) query sql, return html snippet.
- [/w/demo/a.db](/w/demo/a.db) query sqlite3 db file, return html snippet.
- [/w/demo/a.tab?v=tab/curl](/w/demo/a.tab?v=tab/curl) query sql, return multi-column text.

## chart

- [/w/demo/rand-ts.sh?v=ts](/w/demo/rand-ts.sh?v=ts) time series graph, [/w/demo/rand-ts.sh?v=read](/w/demo/rand-ts.sh?v=read) for raw file.
- [/w/demo/profile.data?v=flame](/w/demo/profile.data?v=flame) flame graph.

## upload/edit file

- [/w/demo/a.jpg?v=fops](/w/demo/a.jpg?v=fops) interactive upload local file.
- [/w/demo/a.org?v=upload&file=abc](/w/demo/a.org?v=upload&file=abc) upload simple file.
- [/w/demo/a.txt?v=append&text=hello](/w/demo/a.txt?v=append&text=hello) append text
- [/w/demo/a.org?v=code](/w/demo/a.org?v=code), ctrl-alt-s to save file

## directory view

- [/w/?v=dir](/w/?v=dir) expand sub directory.
- [/w/?v=dir2](/w/?v=dir2) expand sub directory.
- [/w/?v=tar](/w/?v=tar)  download directory as tar
- [/w/?v=sitemap](/w/?v=sitemap) download file list as xml

## encrypt in js

- [/w/demo/a.enc?v=enc](/w/demo/a.enc?v=enc) view encrypt file
- [/w/demo/a.tips?v=code](/w/demo/a.tips?v=code) store decrypt tips
- mount an encrypted directory via `fstab` with `Enc` type, e.g. `/nvr Enc nvr`,
  the on-disk file/dir names and content are obfuscated by `ext/encrypt/enc_store.py`.

## audio/img/video view

- [/w/demo/?v=expo](/w/demo/?v=expo) list as audio/img/video/text etc.
- [/w/demo/?v=album](/w/demo/?v=album) find `.meta` dir, list `.meta/cover.jpg`
- [/w/demo/a.plist?v=plist](/w/demo/a.plist?v=plist) play list edit.
- [/w/demo/a.mp4?v=video](/w/demo/a.mp4?v=video) play single video file.
- shared playback state is broadcast on TCP port 23554 via `servers/timestamp-server.py`
  (launch separately); media views post state through `ext/media/view/state_reporter.js`.

## javascript API

- [/w/demo/a.org?v=head](/w/demo/a.org?v=head)
- [/w/demo/a.org?v=read](/w/demo/a.org?v=read)
- [/w/demo/?v=find](/w/demo/?v=find) supports `t=video` / `t=audio` filters.
- [/w/demo/a.org?v=del](/w/demo/a.org?v=del)
- [/w/demo/a.org?v=write](/w/demo/a.org?v=write)
- [/w/demo/a.org?v=mv&dest=/w/demo/b.org](/w/demo/a.org?v=mv&dest=/w/demo/b.org) rename within one store.
