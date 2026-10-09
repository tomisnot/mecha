# -*- coding: utf-8 -*-
"""⭐ **端到端目标场景判据** —— 这一条是"整台机器能不能开"的验收，不是零件验收。

## 为什么单独立一条（本工程最贵的一课）

在它之前，我们有 29+22+19 项**零件判据**，全部绿；而**那个 web 面板从来起不来**：

    status_page.py:381   self.controller.sites()      ← 把【属性】当【方法】调
    launch.py:285        self.sites = sites           ← 真实现是属性
    ⇒ TypeError: 'tuple' object is not callable ⇒ 页面起不来

⭐ **为什么 29 项判据一条都没抓到**：

| 原因 | 说明 |
|---|---|
| 判据只调 `render_page(stub)` | 而 `render_page` **不碰 `sites`**（读 `state()["sites"]`）⇒ 那一行**从未执行** |
| 替身把 `sites` 写成**方法** | 与页面**同错**、与真实现**不同** ⇒ 在坏实现上**放行**（框架自己的 R12「替身失真」） |
| "真装配"只跑到 `--dry-run` | `--dry-run` 在**起页面之前**就 `return 0` ⇒ "起页面/起聚合/挂后端"整段从未走过 |
| 所有判据都是**单站点** | 而需求是**多项目** ⇒ `sites[0]` 那种缺陷**结构上测不到** |

⇒ 所以本判据**故意走真路**：**真起 launcher 进程**、**真起 `StatusPage`**、**真 HTTP 取页面**、
**真多站点**、**真 POST**。⚠ 它慢（十几秒）且要起进程 ⇒ **只在全档跑**。

## 它钉住的目标场景（照三层架构 §1/§1.1/§7/§8）

1. ⭐ **多站点一起管**：两个站点、两个服务，**都**进同一个 supervisor；
2. ⭐ **真页面**：`GET /` 返回 200，且**列出两个站点的两个服务**（`sites[0]` 那种实现会红）；
3. ⭐ **一个 dsh**：`POST /start` 之后，child 表里 dsh 只有**一个**，且**排在服务之后**；
4. ⭐ **热更新**：`POST /service off → on` ⇒ 该服务**新 pid**、别家不受影响；
5. ⭐ **切写权**：没有控制端点的软件 ⇒ `POST /mode` **如实报失败**（不许静默"成功"）；
6. ⭐ **dsh 的存活滑块**：关 → 开 = **重启 harness**（新 pid、仍只有一行 dsh）。

## Known Limitations and Deferred Work

- ⚠ **判据的软件是 mock**（真起进程但不起真软件）：本判据钉的是**编排/页面/HTTP**这条链路，
  不是各家软件的领域行为（那些归各仓自己的判据）。
- ⚠ **不测 dsh 真起来**：声明里 dsh 的 command 指向 mock（真 dsh 会开浏览器、几十秒）。
- ⚠ **没测**"改一行真代码 ⇒ 新版本生效"那个字面意义的热更新：这里测的是
  "**关→开 = 新进程 pid 变了**"（那是热更新成立的结构前提）。
- ⚠ **dsh 那一行**：本判据在**启动前**（③）与**启动后**（⑦）都断言它出现在页面上
  （§7 的定稿形态：控制台**始终**列出 dsh 一行），并在 ⑧ 真去动它的滑块。
- ⚠ **Windows 上读子进程 stdout 走文件**（不走管道）：沙箱下管道 stdio 会 EPERM，
  而且框架自己的纪律就是"日志落文件"。
"""
from __future__ import annotations

import http.client
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILS: list[str] = []


def _check(name: str, cond: bool, extra: object = "") -> bool:
    tag = "✔" if cond else "✖"
    print(f"  {tag} {name}" + ("" if cond else f"   ← {extra}"))
    if not cond:
        FAILS.append(name)
    return bool(cond)


#: 极简 mock 软件：写端口文件（供"新鲜度"就绪判据）后长睡。
MOCK = r'''
import sys, time
from pathlib import Path
tag = sys.argv[1] if len(sys.argv) > 1 else "?"
if tag != "dsh":
    #: ⚠ 就绪判据是"端口文件**相对 spawn 前变过**" ⇒ 这一步必须真发生。
    Path(".mcp-port").write_text("1", encoding="utf-8")
print("MOCK-UP %s" % tag, flush=True)
time.sleep(600)
'''

#: 一个站点的声明（服务 + dsh **都用 mock**，免得真起 dsh/真起软件）。
#: ⚠ **dsh 是全生态一个、由站点们共同贡献**（§1.1.1）⇒ 两站点的 dsh **command 必须相同**，
#: 各自贡献自己的 `plugin_artifacts`/`env`（模拟"每个项目挂自己的监控面板"）。
SITE_TMPL = '''\
from pathlib import Path
from mecha.site import DSHSpec, ReadySpec, Service, Site

ROOT = Path(__file__).resolve().parent
_PY = {py!r}
_MOCK_DSH = {mock_dsh!r}

SITE = Site(
    name={name!r},
    services=(
        Service(id={sid!r}, brief={brief!r},
                argv=(_PY, str(ROOT / "mock_child.py"), {sid!r}),
                cwd=ROOT,
                ready=ReadySpec(port_file=".mcp-port", timeout_s=30.0),
                #: ⚠ `data_root` 要给：控制端点按它定位 `.control-port`（给 None 时框架要
                #: **说得清**而不是漏一个 TypeError —— 判据 ⑥ 钉的就是那句话）。
                data_root=ROOT,
                has_gui=True,
                env={{"PYTHONUNBUFFERED": "1"}}),
    ),
    #: ⭐ 只贡献"我的面板"，不管 dsh 的身份（command 与另一站点相同）。
    dsh=DSHSpec(command=(_PY, _MOCK_DSH, "dsh"), cwd=ROOT,
                plugin_artifacts=(ROOT / "panel.mjs",),
                env={{"PANEL_" + {sid!r}: str(ROOT)}}),
)
'''


def _http(port: int, method: str, path: str, body: str = "") -> tuple[int, str]:
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        c.request(method, path, body=body,
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
        r = c.getresponse()
        return r.status, r.read().decode("utf-8", "replace")
    finally:
        c.close()


def _wait_url(log: Path, proc: subprocess.Popen, deadline_s: float = 60.0) -> int | None:
    """等 launcher 在日志里打出页面 URL（**读文件，不走管道** —— 沙箱下管道会 EPERM）。"""
    end = time.time() + deadline_s
    pat = re.compile(r"http://127\.0\.0\.1:(\d+)/")
    while time.time() < end:
        if proc.poll() is not None:
            return None
        try:
            txt = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            txt = ""
        m = pat.search(txt)
        if m:
            return int(m.group(1))
        time.sleep(0.25)
    return None


def check_end_to_end_two_sites() -> None:
    td = Path(tempfile.mkdtemp(prefix="mecha-e2e-"))
    mock = td / "mock_child.py"
    mock.write_text(MOCK, encoding="utf-8")
    a, b = td / "siteA", td / "siteB"
    a.mkdir()
    b.mkdir()
    for d in (a, b):
        (d / "mock_child.py").write_text(MOCK, encoding="utf-8")
        #: ⚠ 面板产物要**真的存在**（`DSHSpec.validate` 会对不存在的产物**响亮失败**）。
        (d / "panel.mjs").write_text("// 假装是本站点的 dsh 插件产物\n", encoding="utf-8")
    (a / "mecha_site.py").write_text(
        SITE_TMPL.format(py=sys.executable, mock_dsh=str(mock), name="SiteA",
                         sid="AAA", brief="站点甲的软件"),
        encoding="utf-8")
    (b / "mecha_site.py").write_text(
        SITE_TMPL.format(py=sys.executable, mock_dsh=str(mock), name="SiteB",
                         sid="BBB", brief="站点乙的软件"),
        encoding="utf-8")

    log = td / "launcher.log"
    env = dict(os.environ)
    env.pop("MECHA_SITE_ROOTS", None)          # ⚠ 用 --root 显式给，免得受外层环境干扰
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    proc = None
    try:
        with open(log, "w", encoding="utf-8") as fh:
            proc = subprocess.Popen(
                #: ⚠ cwd 用临时目录 ⇒ `_mecha_runtime/` 落在那里，**不脏框架仓**。
                [sys.executable, "-m", "mecha", "--no-browser",
                 "--root", str(a), "--root", str(b)],
                cwd=str(td), stdout=fh, stderr=subprocess.STDOUT, env=env)
            port = _wait_url(log, proc)
            _check("① ⭐ launcher 起来了、报出页面 URL（真进程 + 真页面）",
                   port is not None, f"日志尾部={log.read_text(encoding='utf-8',errors='replace')[-300:]!r}")
            if port is None:
                return

            # ---- ② 真 HTTP 取页面：⭐ 必须列出【两个站点】的【两个服务】
            code, html = _http(port, "GET", "/")
            _check("② 真页面 HTTP 200（⚠ 这一条就是当初起不来的那一行钉的）",
                   code == 200, f"code={code}")
            _check("② ⭐ 页面列出**站点甲的服务 AAA**（多站点：`sites[0]` 实现会红）",
                   "AAA" in html, f"页面里没有 AAA；长度={len(html)}")
            _check("② ⭐ 页面列出**站点乙的服务 BBB**（跨站点都被列到）",
                   "BBB" in html, f"页面里没有 BBB")
            #: ⭐ §7 定稿形态：**控制台始终列出 dsh 一行**（它不是"起了才有"的一项）。
            #: ⚠ 原先 `render_page` 只在 child 表里有 dsh 时才渲染 ⇒ 启动前那一行是缺的。
            _check("③ ⭐ 页面在**启动前**就列出 dsh 一行（它是控制台的一项，不是'起了才有'）",
                   re.search(r'class="name">dsh<', html) is not None,
                   "启动前的页面里没有 dsh 行")

            # ---- ④ 真启动：所有站点的服务都进 child 表，dsh 只有一个且最后
            _http(port, "POST", "/start")
            _, sj = _http(port, "GET", "/state.json")
            import json
            st = json.loads(sj)
            ids = [str(r.get("id")) for r in st.get("children", [])]
            _check("④ ⭐ POST /start 后：**两个站点的服务都在** child 表里",
                   "AAA" in ids and "BBB" in ids, f"children={ids}")
            _check("④ ⭐ **只有一个 dsh**", ids.count("dsh") == 1, f"children={ids}")
            _check("④ ⭐ **dsh 排在最后**（服务的产出要先就绪）",
                   ids and ids[-1] == "dsh", f"children={ids}")

            # ---- ⑤ 热更新：关→开 = 新 pid
            def pid_of(sid: str) -> object:
                _, body = _http(port, "GET", "/state.json")
                for r in json.loads(body).get("children", []):
                    if str(r.get("id")) == sid:
                        return r.get("pid")
                return None

            p1 = pid_of("AAA")
            _http(port, "POST", "/service", "svc=AAA&on=0")
            _check("⑤ 关掉 AAA ⇒ 它停了（pid 没了）", pid_of("AAA") is None, f"pid={pid_of('AAA')}")
            _check("⑤ 关 AAA **不影响** BBB（不跨软件共享生死域）",
                   pid_of("BBB") is not None, f"BBB pid={pid_of('BBB')}")
            _http(port, "POST", "/service", "svc=AAA&on=1")
            p2 = pid_of("AAA")
            _check("⑤ ⭐ 再开 AAA ⇒ **新 pid**（热更新的结构前提：每次开都是新进程）",
                   p2 is not None and p2 != p1, f"旧={p1} 新={p2}")

            # ---- ⑥ 切写权：没有控制端点 ⇒ **如实报失败**（不许静默成功）
            code, out = _http(port, "POST", "/mode", "svc=AAA&action=human")
            #: ⚠ 判据只要求"**如实报失败**"（不许静默成功）；措辞由实现定，所以给一组可接受标记。
            #: ⚠ 也**不许**把原始的 `TypeError` 漏给页面（实测漏过：`... not NoneType`）。
            honest = any(m in out for m in ("写权没切成", "没切成", "失败", "❌", "读不到"))
            leak = "not NoneType" in out or "Traceback" in out
            _check("⑥ 切写权：软件**没有控制端点** ⇒ 页面**如实报失败**（不静默成功）",
                   code == 200 and honest and not leak,
                   f"code={code} 片段={out[:200]!r}")

            # ---- ⑦ 面板**始终**列出 dsh 一行（§7：它是控制台的一项，不是"起了才有"）
            code, html2 = _http(port, "GET", "/")
            _check("⑦ 页面上 dsh 一行**始终在**（启动后也在；它是控制台的一项）",
                   re.search(r'class="name">dsh<', html2) is not None,
                   "启动后页面里没有 dsh 行")

            # ---- ⑧ ⭐ dsh 那一行的存活滑块：**关→开 = 重启 harness**（§7 定案）
            #: ⚠ 这条是**补上来的**：滑块一直在页面上（`_dsh_row` 渲染它），
            #: 而它原先走的是"在服务的声明表里找 dsh"那条路 ⇒ 点下去**抛
            #: `unknown_service：没有这个软件 'dsh'`** —— 页面上只多一句红字。
            #: ⚠ 判据原先只断言"dsh 这一行在页面上"（③/⑦）⇒ **从没点过那个滑块**
            #: （"看着能用、按下去报错"的又一例）。
            #: 🔬 红证：把 `LaunchController.set_service_enabled` 里那个
            #: `svc_id == DSH_CHILD_ID` 的分支删掉 ⇒ 下面第 2 条必红
            #: （`pid_of('dsh')` 一直是同一个，停不掉）。
            d1 = pid_of("dsh")
            _check("⑧ 前置：dsh 在跑（pid=%r）" % d1, d1 is not None, f"pid={d1}")
            _http(port, "POST", "/service", "svc=dsh&on=0")
            _check("⑧ ⭐ 关掉 dsh 滑块 ⇒ **它真停了**（pid 没了；不是只改了个意图）",
                   pid_of("dsh") is None, f"pid={pid_of('dsh')}")
            _http(port, "POST", "/service", "svc=dsh&on=1")
            d2 = pid_of("dsh")
            _check("⑧ ⭐ 再打开 ⇒ **新 pid**（关→开 = 重启 harness，与软件滑块同形）",
                   d2 is not None and d2 != d1, f"旧={d1} 新={d2}")
            _check("⑧ ⭐ 面板上那一行仍**只有一行 dsh**（重启不是又起了一个）",
                   [str(r.get('id')) for r in json.loads(_http(port, "GET", "/state.json")[1])
                    .get("children", [])].count("dsh") == 1,
                   "children 里 dsh 不止一个")
    finally:
        if proc is not None and proc.poll() is None:
            #: ⚠ 它自己会连带收尾（Job Object）⇒ 先温和终止，再兜底杀树。
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        import shutil
        shutil.rmtree(td, ignore_errors=True)


def check_merge_dsh_by_contract() -> None:
    """⑦ dsh 合并（§1.1.1）：**插件清单取并集**；**身份不一致 ⇒ 响亮失败**。

    ⚠ 为什么这条要单独有：多站点的第一批实现把 dsh 写成"**只准一个站点声明**"
    ⇒ 而 `site.py` 里 `dsh` 是必填、三家真项目各声明一个 ⇒ **多站点必然报错**。
    ⇒ 契约事后被改成"**站点们共同贡献**"（身份必须一致、插件取并集）。
    本判据钉的就是这个契约，**别让它退回"只准一家"**。
    """
    from mecha.errors import MechaError
    from mecha.launch import merge_dsh
    from mecha.site import DSHSpec, ReadySpec, Service, Site

    def _svc(cid: str, root: Path) -> Service:
        return Service(id=cid, brief="x", argv=("x",), cwd=root,
                       ready=ReadySpec(port_file=".p"), data_root=None)

    def _site(name: str, root: Path, dsh: DSHSpec) -> Site:
        return Site(name=name, services=(_svc(name.upper(), root),), dsh=dsh)

    id_dsh = DSHSpec(command=("dsh",), cwd=Path("/x"), env={"A": "1"},
                     plugin_artifacts=(Path("/p/a.mjs"),))
    other = DSHSpec(command=("dsh",), cwd=Path("/y"), env={"B": "2"},
                    plugin_artifacts=(Path("/p/b.mjs"),))
    child, merged = merge_dsh([_site("s1", Path("/x"), id_dsh),
                               _site("s2", Path("/y"), other)])
    arts = {Path(p).name for p in merged.plugin_artifacts}
    #: ⭐ 阶段② 起那**一个** dsh 上还有**框架自己的**面板插件（由框架的 patch 带上）
    #: ⇒ 这里断言的是"两家的并集**都在**"（不是"只有那两家"），另有一条断言框架那份也在。
    _check("⑦ ⭐ 插件的**并集**：两个站点的 plugin_artifacts 都在（项目各自挂自己的面板）",
           {"a.mjs", "b.mjs"} <= arts, f"arts={arts}")
    from mecha.dsh_panel import framework_plugin_artifact
    _check("⑦ ⭐ **框架自己的**面板插件也在（由框架的 patch 带上 ⇒ 新项目不挂框架插件）",
           framework_plugin_artifact().name in arts, f"arts={arts}")
    _check("⑦ ⭐ 框架的面板参数**进了 dsh 的环境**（声明 → 那份参数那段路）",
           "MECHA_PANEL_SERVICES" in merged.env and "MECHA_PANEL_ROUTE" in merged.env,
           f"env={sorted(merged.env)}")
    _check("⑦ ⭐ `env` 也是并集（两个站点各自的项目知识都带上）",
           merged.env.get("A") == "1" and merged.env.get("B") == "2", f"env={dict(merged.env)}")
    _check("⑦ `cwd` 取**第一个**声明者（站点顺序稳定 ⇒ 不随扫描漂）",
           str(merged.cwd).replace("\\", "/") == "/x", f"cwd={merged.cwd}")
    _check("⑦ dsh child 只有一个、id='dsh'", child.id == "dsh", f"id={child.id}")

    #: ⭐ 身份不一致 ⇒ **响亮失败**（静默取一家 = "我以为起的是我那种"）
    bad = DSHSpec(command=("dsh", "--different"), cwd=Path("/z"))
    try:
        merge_dsh([_site("s1", Path("/x"), id_dsh), _site("s3", Path("/z"), bad)])
        _check("⑦ ⭐ 身份字段不一致（command 不同）⇒ **响亮失败**", False, "竟然没报错")
    except MechaError as exc:
        _check("⑦ ⭐ 身份字段不一致（command 不同）⇒ **响亮失败**",
               exc.kind == "dsh_decl_conflict", f"kind={exc.kind}")


def check_backend_url_single_colon() -> None:
    """⑧ ⭐ 后端 URL **只许有一个冒号** —— 实测三家的探活串自带冒号，而夹具没有。

    ⚠ **这条是补上来的、不是先有的**：实测用户点「启动 ▸」报
    ``backend_protocol：后端 ML（http://127.0.0.1::62499/mcp）协议错``
    —— ⚠ **两个冒号**。根因：`_url_for` 假设探活串是 `http://host{port}/…`（**不带冒号**），
    而三家的都是 ``http://127.0.0.1:{port}/mcp``（**自带冒号**）⇒ 它又补了一个。
    ⇒ ⚠ **三家全中**（AI 那条腿从来没接上过真项目），而判据全绿 ——
    因为**夹具的探活串没有那个冒号**（"替身失真"的又一例）。
    ⇒ 这条判据**按真形状**喂（自带冒号），并钉住"只许一个冒号"。
    """
    from mecha.aggregate_runtime import AggregateRuntime

    class _Svc:
        ready = type("R", (), {"probe": "http://127.0.0.1:{port}/mcp"})()

    url = AggregateRuntime._url_for(None, _Svc(), {"port": 62499})   # type: ignore[arg-type]
    _check("⑧ ⭐ 探活串**自带冒号**时，后端 URL **不许拼出双冒号**",
           url == "http://127.0.0.1:62499/mcp", f"url={url!r}")
    class _Svc2:
        ready = type("R", (), {"probe": "http://127.0.0.1{port}/mcp"})()
    url2 = AggregateRuntime._url_for(None, _Svc2(), {"port": 62499})  # type: ignore[arg-type]
    _check("⑧ 对偶：探活串**不带冒号**时也要对（两种形状都收）",
           url2 == "http://127.0.0.1:62499/mcp", f"url={url2!r}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("[endtoend_selfcheck] ⭐ 目标场景：真起 launcher + 真页面 + 多站点 + 热更新 + 切写权")
    try:
        check_merge_dsh_by_contract()
        check_backend_url_single_colon()
        check_end_to_end_two_sites()
    except Exception as exc:                                # noqa: BLE001 - 判据护栏
        #: ⚠ **崩溃必须转成判据失败**（框架 R 系列的老教训：崩溃冒充"没测出问题"）。
        _check(f"【护栏】目标场景判据抛异常：{type(exc).__name__}", False, str(exc)[:200])
    if FAILS:
        print(f"endtoend_selfcheck: FAIL（{len(FAILS)} 条）：{FAILS}")
        return 1
    print("endtoend_selfcheck: PASS（目标场景整条走通）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
