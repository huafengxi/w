# design

每个URL都定位了一个resource，resource可以看作是抽象的file。根据file的mime type，
不同的类型的文件会映射到另一个path，原始文件的path我们叫 `src_path`, 映射后的path我们叫 `view_path` 。
`view_path` 可以看作是打开 `src_path` 的方式。

整个server由与业务无关的 `core/` 加上按约定加载的 `ext/<feature>/` 组成，
`stores/` 提供 store 实现，配置文件 (`vmap`, `mime`, `stores/fstab`) 是声明式的
真理来源，代码去适配它们。

## file and store

store用来持久化file，接口如下：

```
class AbstractStore:
   def head(self, path): pass                       # 返回文件meta，至少包含 'type'
   def read(self, path): pass                       # 读取整个文件 (目录路径以 '/' 结尾)
   def read_dir(self, path): pass                   # 可选，目录读取的快路径
   def lazy_read(self, path, range_req=''): pass    # 可选，分段读取，返回 (total, start, end, iter)
   def write(self, path, content): pass
   def delete(self, path): pass                     # 可选
   def mv(self, path, new_path): pass               # 可选，仅限同一store内
```

`head` 返回的dict除了 `type` 还可能带 `rpath` / `size` / `modified` 等字段，
`RootStore` (定义在 `stores/store.py`) 会按 `fstab` 把请求路径分发到对应的子store。

## mime type

`store.head(path)` 返回一个dict，字典的 `'type'` 域就是 mime type。
后缀 → mime 的查表在 `mime/` 目录 (`mime/__init__.py` 合并基线 `mime/mime.frag`
与所有 `ext/<name>/mime.frag`)，文本文件也可以在开头显式指定：

```
 -*- type=script -*- # 显式指定mime type
```

URL query string 里的 `v` 优先级更高，例如 [?v=read](?v=read)。POST body 会被当作
query string 一起并入 args。

## view_map

`mime type` 到 `view_path` 的映射叫 view_map，服务时通过 [/vmap/](/vmap/) 暴露。

view_map 也是按约定拼装：`vmap/__init__.py:build()` 读入 `vmap/vmap.frag`
(核心映射) 再合并所有 `ext/<name>/vmap.frag` (ext 追加自己的映射)，
`core/handler.py:VMap` 在启动时直接调用 `vmap.build()` 加载。

view 文件本身分布在 `core/view/` 与各 `ext/<feature>/view/` 目录。
`core/view/` 只保留通用组件 (`template/404/code/iframe/split` 等)，每个 ext
自带自己的 view 资源。

## script

只有 mime type 为 `script` 的 python 文件才会被当作 RPC 执行。
script 里应定义 `interp(store, **args)` 函数；当 `view_path` 是 script 时，
handler 会调用 `interp(...)` 产生响应。

script 分布在 `core/rpc/` (核心只读 rpc，如 `core_read.py`) 与
`ext/<feature>/rpc/` 目录。执行时，`core/handler.py:run_script()` 给 script
注入一个固定的全局命名空间 (标准库 + `popen` / `sub` / `response_part_file`
/ `build_dict` / `dict_updated` / `NULLFD` 等 helper)。script 需要 ext 自己
的模块时，直接 `from ext.<feature>.xxx import Foo` 即可 (例如
`ext/media/rpc/album_rpc.py` 里 `from ext.media.album import AlbumDB`)，
没有额外的注册步骤。

`interp` 的返回值应该是 `(meta, content_iterator)` 的 tuple，也可以只是一个
字符串，字符串会按 `text/plain` 处理。

## RootStore和fstab

`stores/fstab` 把不同类型的 store 挂载到 root store。fstab 是单个声明式文件
(没有 fstab.frag)，每行 `挂载点 类型 参数...`。目前 `stores/` 下提供的
store 实现：

1. DictStore  — 通用的内存键值 store (空 dict 起步，通过 write 更新)。
2. DirStore   — 把本地目录挂载为 store。
3. EncStore   — 在 DirStore 基础上对文件名与内容做混淆，实现简单的加密目录。
4. WebDavStore — 通过 `webdav4` 把远端 WebDAV 挂载为 store，支持 range read。
5. CmdStore   — 把 read/write/delete 委托给外部命令执行。

store 类按命名约定自动加载：`build_root_store` 解析出 fstab 里的类型 `T` 后，
`import stores.<t>_store` 并取 `TStore` 类，然后 `cls(*args, **kw)` 实例化。
因此新增 store 只要在 `stores/` 下放一个 `<t>_store.py` 并在 fstab 里加行即可，
不需要注册代码。

## 扩展的约定

一个 ext 就是 `ext/<feature>/` 目录，可选包含：

- `vmap.frag` / `mime.frag` — 会被自动合并进 `vmap/` 与 `mime/`。
- `sh.rc.frag` — bash 片段 (alias / 函数)，被 `ext/shell/sh.rc` 自动 source。
- `rpc/*.py` — script，供 vmap.frag 里的映射指向。
- `view/*` — 模板 / JS / CSS 等静态资源。
- `bin/` — 会被 `core/server.py:set_path()` 追加到 `PATH`，供 script/CmdStore 调用。
- 任意 `*.py` 模块 — script 通过 `from ext.<feature>.xxx import ...` 直接使用。

没有 `register.py`、没有 `ext=` 环境变量预导入；扩展纯粹按目录约定被发现，
不需要写注册代码。

## shell rc

`ext/shell/rpc/sh.py` 用 bash 流式执行命令，启动时把 `BASH_ENV` 指向
`ext/shell/sh.rc`。这个基线 rc 会遍历并 source 所有 `ext/<name>/sh.rc.frag`，
从而让各 ext 以约定的方式往 bash 环境里注入 alias / 函数 (如 `ext/sql` 提供
表查询别名、`ext/report` 提供 ido 调度命令)，无需在 sh.py 里硬编码。frag
加载完后 `sh.rc` 还会 source 同目录下的 `.sh.rc` (已被 `.gitignore` 忽略)
作为本地覆盖；例如 `ext/report` 的 `ido_report_cmd` 需要 `ido_root` 环境
变量指向 ido 根目录，未设置时会向 stderr 报错并返回 1，没有默认值。

## playback state broadcast

`servers/timestamp-server.py` 独立启动，监听 TCP 23554。
浏览器端的 `ext/media/view/state_reporter.js` 在播放/暂停/定时回报时把状态写入
`playing-state.json` ，server 直接监视该文件并向所有连接的客户端广播实时进度，
用于多端同步播放。
