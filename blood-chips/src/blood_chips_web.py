#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""带血筹码 · 监测面板（只读数据 + 起服务）

只读聚合 blood_chips_*.json，起一个零依赖的本地 Web 面板：
  GET  /                     面板 HTML（同目录 blood_chips_web.html）
  GET  /api/data             聚合 JSON（广度 / 候选 / 情绪 / 观察池）
  GET  /api/deep             深度层（宏观 / 技术面 / 基本面），13h 未更新算 stale
  GET  /api/discount         折价区（主流桶 / meme 桶档位 + 明细），26h 未更新算 stale
  GET  /api/brief            最近一次 agent 简报 + build（页面靠它发现自己是不是旧版 → 自动刷新）
  GET  /api/lookup?q=        · 币名/代码：**按可达性排序的多源**（NAME_SOURCE_ORDER）
                               CoinGecko /search（本地 /coins/list 索引命中就零请求）→ CoinPaprika /search
                               （中文可搜、按 rank 升序）→ 本地符号索引（只吃内存，不发现场请求）
                             · 合约地址（0x… / Solana base58）：**按可达性排序的多源**
                               （ADDRESS_SOURCE_ORDER = **GMGN market search** → DexScreener →
                               GeckoTerminal /search/pools（一次定链）→ GeckoTerminal 逐链 → CoinGecko）：
                               **GMGN market search -q <地址> 是第一顺位**（独立主机 + 独立熔断桶：
                               代理把别的源全打死时它照样能出结果；命中**必须地址完全一致**才认，
                               地址不符/链对不上 → 当没命中，接着试下一档）；
                               GeckoTerminal /networks/{net}/tokens/{addr}/pools 是逐链兜底（全链可用），
                               base58 先试 solana、0x **并行**探 eth/base/bsc/arbitrum/polygon/optimism/gnosis
                               （ThreadPoolExecutor，4 线程，单链一次请求）；命中多条链**全部列出**
                               （每链一行，按 reserve_in_usd 从大到小，带链名徽章）；
                               某条链 429 / 连不上**绝不让整个源失败**（继续等别的链，missing 里说明）；
                               某链「响应 200/404 但没有池子」→ 负缓存 5 分钟（⛔ 只放内存）；
                               全试完没有就写明**每条链是空还是错**（预算到点还要写明已试/未试的链）；
                               DexScreener（不限链）/ CoinGecko（老路径）只是它的备用；
                              前一个源 0 条 / 报错才试下一个，每个源独立熔断桶；
                               ⚠️ **用户给的地址是池子**（GT /search/pools 命中的 id 地址 == 查询
                               地址、而 base/quote 是**别的**地址）→ 自动还原成代币，**改动①（2026-09-16）**：
                               取代币**只认**池子 relationships 的 base_token / quote_token（GT
                               /networks/{net}/pools/{pool} 与 /search/pools 是同一份），
                               **绝不用池名**（"BNC4 / 4Stock"、"CAKE-LP / BNC4" 都是文字，不等于符号）；
                               候选代币**必须自证**：/networks/{net}/tokens/{代币}/pools?page=1 非空才算
                               查得到池子 → base 空就试 quote；**LP 代币黑名单**（*-LP / CAKE-LP / UNI-V2 /
                               SLP / *V2 / *V3，symbol 或 name 命中）绝不当最终代币（另一侧可用就用另一侧）；
                               两侧都不可用 → 报「这个池子的两侧代币在 GeckoTerminal 都查不到池子」+ 两侧
                               地址与 symbol，**绝不猜**；候选顺序**固定 base → quote** → 同一输入连查 N 次
                               symbol / token_address 完全一致（不靠源顺序、不靠列表顺序）；
                               ⚠️ **改动④（2026-09-16 真机事故）：429 ≠ 空。** 自证那一枪的结论必须分
                               ok / rate(429) / down(超时·5xx·连不上) / nodata(200 空) / notfound(404)：
                               只有 **nodata / notfound** 才算「真没有池子」（才允许换另一侧、才允许说
                               「两侧都查不到池子」，且那句文案必须写明「本次拿到了确定回答（HTTP 404 /
                               空），不是限流」）；**rate / down 绝不当成「没有池子」** → 降级**接受池子里
                               明写的 base_token**（不是猜的），notes 写明「限流中（HTTP 429），未能自证」，
                               结构化 blocked_by 带 {source:"geckoterminal", kind:"rate", retry_after_s}；
                               （base 侧没答话就直接按 base_token 降级，**不再拿另一侧去撞同一个 429**；
                               两侧都没答话时口径一样 —— 按 base 处理，见改动④策略 2）
                               symbol 仍只取 /tokens/{代币}，取不到（限流）就 null + 说明，**绝不拿池名顶替**；
                               改动⑤：自证结论按 (链, 池子) 在内存里缓存 10 分钟（rate / down 只缓存 60s，
                               额度一恢复就能重问），已经复用过的 /search/pools 那一枪继续复用；
                               ⚠️ **改动①（2026-09-16 真机）：身份判定被限流时绝不许退化成 0 条。**
                               GT /search/pools 限流（429）/ 连不上时，「这个地址是池子还是代币」**没判定
                               出来** → **降级继续**（按「代币地址」走上面那条多源顺序），结果行标
                               pool_precheck="unknown_due_to_rate" + notes「GeckoTerminal 限流中，未判定
                               这个地址是池子还是代币；已按代币处理」+ blocked_by(geckoterminal/rate)；
                               后续某个源明确回答「这是 LP / 池子」（GMGN 命中 CAKE-LP 黑名单）→ 再切到
                               池子→代币的还原逻辑，还原不出来就**保留一条降级结果**（results ≥ 1）；
                               只有 GT 给出**确定回答**（200 空 / 404 = 不是池子）才照旧丢掉 LP 行；
                               ⚠️ **改动③：GT 每次调用间隔 ≥ GT_MIN_INTERVAL(2s)、429 按 Retry-After 退避，
                               且同一地址查询里 GT 的**逻辑调用** ≤ GT_CALLS_PER_QUERY_MAX(3, env 可覆盖)**
                               —— 超了停手 + missing 写「本次 GT 调用已达上限，可能没取全」（逐链兜底
                               那 7 发不占这个额度：它自带 per-chain 节流 / 负缓存 / 查询预算）；
                              链以池子的 network 为准，notes 写「你给的是池子 0x…（链 bsc），
                               已按基础代币 0x…（BNC4）分析」；token_address 没定时**不发** GT 日线
                               （改动③：省额度、少误报 404 —— 也绝不拿池子地址 / LP 代币冒充代币）；
                               下游取数（GMGN kline / Binance SYMBOLUSDT / GT 日线）一律用**代币**地址
                               与**代币** symbol（币安未上市那句文案因此说的是真 symbol）；
                               字段只用真有的（缺的 null + 一句原因，绝不猜）；再拿 CoinGecko 补 mcap/距ATH
                               （补不到就只用主源并标注）；CoinGecko 熔断也不拦地址查询
                             结果行**自带 chain**（GeckoTerminal /search/pools 的 data[].id 前缀 =
                             network，如 "bsc_0x…" → chain="bsc"；DexScreener/GMGN 行同样带）
                             —— 下游按「链 + 合约地址」取日线全靠它，行里绝不写 None；
                             结果**第 1 条**再挂 struct_rr：结构盈亏比（日线 **GMGN kline(1d) → Binance →
                             GeckoTerminal**；GT 这档有**两条腿**：优先用结果行的 chain + token_address
                             直接打 /networks/{chain}/tokens/{addr}/pools（**按 reserve_in_usd 降序**）
                             → **依次**对前 GT_POOL_TRY_MAX(3) 个池子试 ohlcv/day?limit=200
                             （一个池子 404 / 空数据**不代表这个币没日线**；试过哪些池子、各什么结果
                              全部进 meta.attempts；熔断立刻停手、429 先按 Retry-After 退避重试
                             （最多 GT_429_RETRY_MAX 次），退完还是 429 才停手 +「稍后自动可查」）
                             （**不依赖 GMGN / 也不依赖 Binance**，BSC/Base/ETH/SOL 都适用），
                             再回落原来的按 symbol 找池；GMGN 的 time 是毫秒（复用已有 ms 判定）；
                             每源**独立子预算** STRUCT_SRC_BUDGET（GMGN 5s / Binance 5s / GT 10s，
                             env STRUCT_SRC_BUDGET_* 可覆盖；子预算之和 > STRUCT_BUDGET_SEC 时
                             **自动按比例整体缩小**，绝不静默饿死某一个，实际值进 debug.src_budget）——
                             2026-09-16 真机（GMGN 封的是共享出口 IP → **被封是常态**）：GT 必须能
                             独立顶住 → GT 子预算 10s，且**主池 OHLCV 那一枪优先**（列池子只用
                             子预算减 GT_OHLCV_RESERVE_SEC）；GT 429 按 Retry-After 退避重试
                             （最多 GT_429_RETRY_MAX=2 次，仍 429 → kind=rate +「稍后自动可查」）；
                             一个源挂死绝不把后面的源饿死，熔断/限流立刻跳下一个；
                             样本 ≥ STRUCT_YOUNG_MIN_BARS(7) 根就照常出数（不再因为只有 8 根整块失败）：
                             够 16 根 = 直接胜出，都不够就退回**根数最多**的那条，meta 写明
                             「样本仅 N 根日线 / 窗口 = 近 w 日最低 / 每条源腿的 (源, 根数, kind)」；
                             BSC / 币安未上市的链现在也能出结构盈亏比；
                             ⚠️ 结构块**只在所有日线候选源（GMGN/Binance/DexScreener/GT）全挂时**才跳过
                             —— CoinGecko / DexScreener 熔断**绝不连坐** GMGN（2026-09-15 真机事故）；
                             0 条结果的 missing 逐条写「源名（错误类型）」+ 一句「换 Clash 节点 / 用合约地址」；
                             ⚠️ **改动②（2026-09-16 真机：0 条被缓存了，几十秒后才恢复）：查询词缓存的
                             口径 —— results 非空走原有 TTL（成功 10 分钟 / 失败或降级 20 秒）；
                             results == [] 时**最多 30 秒**，且这次失败里只要有**任何一条腿**是
                             rate（限流 / 熔断）或 down（连不上 / 超时）→ **一秒都不缓存**（下一次直接
                             重新发请求）：0 条不是「可复用的结论」，上游答不了话时更不能当天条缓存；
                             GMGN 熔断中 → 响应带 gmgn_ban_until（解封时刻，ISO）+ missing 里写明
                             「GMGN 限流中，预计 HH:MM 恢复（用其它源先顶着）」（前端同一口径，只排版）；
                             参照位跟**币龄**匹配：bars_n<60 → 下行参照位用**近期窗口低点**
                             「近 w 日最低」(w = max(14, bars_n//2) 且 w ≤ bars_n：
                             lo_ref="lo_recent" / lo_ref_price / lo_ref_date / lo_ref_window /
                             lo_window_label / listing_age_days)；上市以来最低（lo_since_listing）
                             只当参考信息、**绝不进算式**；bars_n<7 → 参照位 null+原因，
                             bars_n<200 不叫 MA200（ma_window/ma_label），bars_n<30 均线 null+原因；
                             标签写死「结构口径，非买卖建议」；只算第 1 条，⛔ 只放内存、不落盘）
                             行情按「币 id」缓存 10 分钟（⛔ 只放内存、一个文件都不落，换词命中同一个币 → 零请求）
                             拿不到行情时依次兜底：陈旧内存缓存 → CoinPaprika 全量快照（按 symbol 对号）
                             → DefiLlama 直连（⛔ 不走代理，只补现价）→ 记「缺失」
                             结果都带 source / source_age_s / upstream（熔断状态那行小字）
                             熔断**分源**（GeckoTerminal 逐链兜底一个、GeckoTerminal /search/pools
                             一个、CoinGecko 一个、DexScreener 一个、CoinPaprika 一个）：
                             连续 4 次**连接类**失败 →
                             熔断 25 秒（期间该源零上游请求、<50ms 返回），到点半开只放 1 个请求，
                             成功立刻闭合并清零；**429 只单独计数，绝不进熔断**（额度 ≠ 网断）
                             查询预算：名称搜索 LOOKUP_BUDGET(12s)，地址查询 ADDRESS_LOOKUP_BUDGET(15s)
                             （地址一次定链就够，逐链探测只当兜底）；预算耗尽要写明
                             「已试过哪些源、各自为什么不行」
                             结构取数**和行情富化并行**、用**自己的** STRUCT_BUDGET_SEC(20s，
                             env 可覆盖；绝不和行情取 min —— 2026-09-16 真机事故：行情富化把 12s
                             吃光 → 结构盈亏比一次请求都没发就写「超出本次查询预算」)；
                             deadline **从交接点（拿到第 1 条身份）才起算**（老口径从请求开始算 →
                             行情侧 10+ 秒才交接时结构一动手就秒超；交接始终没来则用
                             请求开始 + LOOKUP_TOTAL_TIMEOUT_SEC 兜底）；
                             整体上限 = max(两份预算) + 2s 余量（默认 22s）；同一请求内结构取数
                             只 1 个并发任务；全部源不可用 / 没这个币的日线，文案分开写清
                             （nodata = 「有这条交易对，但只有 N 根日线」，和「未上市」一眼可分）；
                             失败时的 meta 带 **blocked_by=[{source,kind,retry_after_s}]**（谁在限流、
                             上游说多久恢复）→ 前端文案只照抄这个结构化字段，**绝不自己编数字**；
                             改动⑥：结果行 / 响应顶层同样带 blocked_by（如 GT 自证被 429 挡住时的
                             {source:"geckoterminal", kind:"rate", retry_after_s}），并并进 struct_rr.meta
                             —— 前端不新增逻辑，照旧只照抄 blocked_by 与 notes；
                             struct_rr.debug.{timings, src_budget} 给诊断（纯数字，不参与算法）
  POST /api/refresh          后台串行跑 discover → monitor → discount → deep
  POST /api/watchlist/add    加一条 crypto 进观察池（读→改→原子写，写前备份）
  POST /api/watchlist/remove 按 id 删一条
  GET  /api/costbasis/resolve?q=  查询词/合约地址 → {ok, chain, address, symbol, why,
                                  resolved_name, resolved_chain, resolved_address,
                                  resolved_liquidity_usd, resolved_rank, source}
                                  0x / base58 地址：**先 GMGN market search -q <地址>**（1 次请求，
                                  命中且地址完全一致 → source="gmgn search"）→ 没命中再 DexScreener
                                  反查链（只认 CHAIN_INFO 支持的链）；地址解析总预算 RESOLVE_BUDGET(12s)、
                                  其中 DexScreener ≤ RESOLVE_DEX_BUDGET(6s)，超预算即 ok:false + 写明试过谁；
                                  币名/代码：**先 GMGN market search**（按 liquidity/mcp 降序、
                                  蜜罐排最后并注明，source=gmgn_search）→
                                  **复用 /api/lookup 的同一套排序**取第 1 条（市值排名 +
                                  桥接/包装版垫底）→ 它自带受支持链的合约就直接用（source=lookup#1）→
                                  否则 GeckoTerminal /search/pools 按流动性降序第 1 条
                                  （source=geckoterminal_search）→ 最后才 /coins/{id} 的 platforms
                                  按 ethereum > base > arbitrum > polygon > optimism > gnosis >
                                  solana 选链（source=coingecko platforms）；
                                  **选不到就 ok:false + 写明每条路试过什么；绝不猜链、也绝不拿搜索第 2 条**
  POST /api/costbasis/start   body {q} 或 {chain,address}(+source) → 起后台任务（{job_id}）；
                              同一币已有结果/在跑就复用，**同一进程最多 1 个任务在跑**，多的排队；
                              分析在内存里跑 blood_chips_costbasis.analyze()（不用 CLI 子进程）；
                              source=auto（默认，GMGN 优先→失败回落链上重建）/ gmgn（1 次请求）/ onchain
                              ⚠️ GMGN 配额铁律在 costbasis 那侧（≥3s/次、≤8 次/分、内存缓存 10 分钟、
                              RATE_LIMIT_BANNED → 熔断到**报文里的解封时刻 + 10s 余量**
                              （clamp 到 [60s, 900s]；报文没给时间才退回 900s）且**零请求**、
                              绝不落盘；解封时刻以 gmgn_ban_until 暴露给前端显示「预计 HH:MM 恢复」）
  GET  /api/costbasis/status?job_id=  {state:running|done|error, progress, result, why}
                              结果进 _COSTBASIS_CACHE（TTL 15 分钟，⛔ 只放内存、绝不落盘）
  GET  /api/status           采集状态 + 日志尾部

任何文件/字段缺失都降级为 null 或「缺失」，不抛异常、不编数字。

用法: python3 blood_chips_web.py [--port N]
环境: BLOOD_CHIPS_WEB_PORT(默认 8767) / BLOOD_CHIPS_DIR(默认脚本所在目录)
      BLOOD_CHIPS_PROXY(默认 http://172.28.80.1:7890)
"""
import concurrent.futures
import copy
import datetime
import hashlib
import http.client
import io
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("BLOOD_CHIPS_DIR") or BASE
HTML_PATH = os.path.join(BASE, "blood_chips_web.html")
DEFAULT_PORT = 8767

# ---------------------------------------------------------------- 面板构建号
# 真机教训（2026-09-15）：面板重启 / 代码更新后，用户**开着的旧页面**里跑的还是旧 JS
# → 「功能加了但我看不到」（要用户手动硬刷新才对）。所以启动时算一个构建号：
# 模块文件（mtime + size）的 sha1 前 12 位；/api/brief 带上它，页面里也内联一份，
# 两边不一致 → 顶部弹「面板已更新，正在刷新…」→ 1 秒后自动 reload。
BUILD_FILES = ("blood_chips_web.py", "blood_chips_web.html", "blood_chips_costbasis.py")


def _compute_build(files=None):
    """构建号（纯内存计算，⛔ 不落盘）。每次调用都重新 stat —— 文件一改，值就变。"""
    h = hashlib.sha1()
    for name in (BUILD_FILES if files is None else files):
        path = name if os.path.isabs(name) else os.path.join(BASE, name)
        try:
            st = os.stat(path)
            h.update(("%s|%d|%d;" % (os.path.basename(path), int(st.st_mtime),
                                     st.st_size)).encode("utf-8"))
        except OSError:
            h.update(("%s|missing;" % os.path.basename(path)).encode("utf-8"))
    return h.hexdigest()[:12]


BUILD = _compute_build()

CANDIDATES_JSON = os.path.join(DATA_DIR, "blood_chips_candidates.json")
DATA_JSON = os.path.join(DATA_DIR, "blood_chips_data.json")
WATCHLIST_JSON = os.path.join(DATA_DIR, "blood_chips_watchlist.json")
DEEP_JSON = os.path.join(DATA_DIR, "blood_chips_deep.json")
DISCOUNT_JSON = os.path.join(DATA_DIR, "blood_chips_discount.json")

# 产物多久算过期（前端给黄字提示；不编时间，读不到时间戳就当过期）
DEEP_STALE_H = 13.0       # 深度层：一天跑两次
DISCOUNT_STALE_H = 26.0   # 折价区：candidates 一天一次 + 缓冲
CRON_DIR = os.path.expanduser("~/.hermes/cron/output/c309f4e6fdbd")

LOG_KEEP = 200          # 内存里保留的日志行数
LOG_TAIL = 40           # /api/status 返回的尾部行数
REFRESH_TIMEOUT = 1800  # 单条命令超时（秒）


def _env_float(name, default):
    """env 覆盖的秒数开关：没设 / 不是数 / ≤0 → 用默认值（一个坏 env 绝不让服务起不来）。"""
    try:
        val = float((os.environ.get(name) or "").strip())
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


# ---- /api/lookup 出站请求 ----
# 本机直连这些境外数据源不通，必须走 Clash；可用 env BLOOD_CHIPS_PROXY 覆盖
PROXY = os.environ.get("BLOOD_CHIPS_PROXY") or "http://172.28.80.1:7890"
# 默认 UA 必须是完整 Chrome UA：CoinGecko 对裸 urllib 不友好
UA_CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

HTTP_TIMEOUT = 8          # 单次请求超时（秒）；交互路径没必要等 20 秒
GECKO_MIN_INTERVAL = 0.5  # CoinGecko 全局串行：相邻请求至少间隔这么多秒
WAIT_RETRY_FAST = 0.30    # 连接类失败（Connection reset / SSL UNEXPECTED_EOF / timeout）
WAIT_RETRY_429 = 3.0      # **只有 429** 才退避这么久
RETRY_MAX = 7             # 重试次数（加上首次共 8 次尝试）
# ⚠️ 2026-09-15 实测：连查 4~5 个词后 /coins/markets 稳定 429，而 429 是**分钟级窗口**——
#    3 秒后重试还在同一个窗口里，8 次尝试全败、12s 预算烧光 → 该币行情整段 null → 面板「全是 —」。
#    所以 429 只再试 1 次，剩下的交给「陈旧缓存 → CoinPaprika 降级」。
RETRY_MAX_429 = 1         # 429 的重试次数（加上首次共 2 次尝试）
# 实测 2026-09-15：本机代理对 api.coingecko.com 单次成功率仅约 50%
# （URLError: Connection reset by peer / SSL UNEXPECTED_EOF）—— 这类失败是**瞬间**返回的，
# 干等 3 秒纯属浪费（3 秒是给 429 用的）。健康时单次 0.52–0.78 秒（中位 0.57 秒），
# 0.25–0.3 秒间隔连打 14 次 14/14 成功、零 429，所以用「快重试 + 多试几次」拿可靠性。
LOOKUP_BUDGET = 12        # /api/lookup **名称搜索**总预算（秒）：超了立刻返回已有结果，缺的写「缺失」
# 地址查询现在是 **GeckoTerminal /search/pools 一次调用定链**（真机 ~1.5s 就够），
# 逐链探测（7 发、最坏 ~10.5s）只当兜底 → 预算从 25 秒降回 15 秒（兜底路径同样受这个预算约束）。
ADDRESS_LOOKUP_BUDGET = 15
# ---- 源顺序（**写死成常量**，便于以后调；前一个源 0 条 / 报错才试下一个）----
# 地址：**GMGN market search -q <地址>（第一顺位）** → DexScreener（不限链，一发）
#       → GeckoTerminal /search/pools（一次调用定链）→ GeckoTerminal 逐链探测（老路径，兜底）
#       → CoinGecko（老路径，最后）
# 2026-09-15 真机事故：代理半死时 GMGN 完全健康（resolve 2.06s 成功），而 DexScreener SSL 超时 /
#   GeckoTerminal 连不上 / CoinGecko 熔断 → 地址查询 0 条（明明 GMGN 能搜到这个币）。
#   → GMGN search **升为第一顺位**：独立主机 + 独立熔断桶，别的源全挂时它照样能出结果。
# 2026-09-16 真机事故①：用户给的其实**是池子地址**（0x4682c6bf…）→ 老口径把池子地址当 token_address，
#   下游按它取日线必然 404。现在 GT /search/pools 命中「id 地址 == 查询地址」时自动还原成 base_token
#   （见 _gt_search_kind / _gt_pool_token_row）—— 结果行照样出，但取数一律用**代币**地址与 symbol。
# 2026-09-16 真机事故②（同一个池子地址**连着查两次结果不一致**：第一次 BNC4 ✓、第二次 CAKE-LP ✗）：
#   根因是「到底哪个源先答话」——GMGN / DexScreener 眼里**池子地址就是个 LP 的 ERC20**，抓到就叫 CAKE-LP，
#   再拿 LP 代币去要池子必然 404。现在：**地址查询先做一次 GT /search/pools 的池子身份前置判定**
#   （_gt_pool_precheck）——是池子 → **只**用池子的 relationships（base/quote）+ 自证 + LP 黑名单，
#   别的源一律不看；不是池子（或 GT 这一枪没答话）才照旧走多源顺序，且 GT 那一档复用这一枪的结果。
ADDRESS_SOURCE_ORDER = ("gmgn_search", "dexscreener", "geckoterminal_search",
                        "geckoterminal", "coingecko")
# 每一档在 missing / 结果行里的名字（0 条结果时用户要一眼看出「是谁挂了」）
ADDRESS_SOURCE_LABEL = {"gmgn_search": "GMGN search",
                        "dexscreener": "DexScreener",
                        "geckoterminal_search": "GeckoTerminal /search/pools",
                        "geckoterminal": "GeckoTerminal 逐链",
                        "coingecko": "CoinGecko"}
GMGN_SEARCH_SOURCE = "gmgn search"      # 结果行的 source（和 /api/costbasis/resolve 同一口径）
# /api/lookup 地址查询里 GMGN 这一档自己的小预算（再和总预算 ADDRESS_LOOKUP_BUDGET 取更早的）
GMGN_ADDR_LOOKUP_BUDGET = 5.0
# 0 条结果时补在 missing 尾巴上的那句建议（照着做就能换条路走，别只留一句「查询失败」）
LOOKUP_EMPTY_ADVICE = "建议：换 Clash 节点；或改用合约地址查询"
# 名称：CoinGecko search → CoinPaprika search（中文可搜）→ GeckoTerminal 池子搜索（补充源，
#       有的币只有池子）→ 本地符号索引（只吃内存）
NAME_SOURCE_ORDER = ("coingecko", "coinpaprika", "geckoterminal_search", "local_index")

LOOKUP_TTL = 600          # /api/lookup **成功**结果缓存 10 分钟
LOOKUP_FAIL_TTL = 20      # ⚠️ 失败/部分失败只缓存 20 秒
# 改动②（2026-09-16 真机）：`results == []` **最多**缓存 30 秒 —— 0 条是「这次没查到」，
#   不是一个可复用的结论（真机：代理抖一下 → 0 条被缓存 → 之后连查 0.0s 照样 0 条，几十秒后才恢复）。
#   而且这次失败里只要**任何一条腿**是 rate / down → 一秒都不缓存（下一次直接重试）。
LOOKUP_EMPTY_TTL = 30
MARKET_TTL = 600          # 按「币 id」的行情缓存 10 分钟（同一币换词查 = 零请求）
#   2026-09-15 实测事故：代理抖一下 → 检索失败 → 这个**失败结果被缓存 10 分钟** →
#   代理恢复了、直连 0.6 秒就好，但从面板看「怎么查都失败（0.00 秒返回）」。
#   失败必须快过期，否则一次网络抖动会毁掉 10 分钟的查询体验。
LLAMA_TTL = 3600          # DefiLlama gecko_id 索引缓存 1 小时（一次 ~8.8MB）
LLAMA_FAIL_TTL = 60       # 索引构建失败后，短时间内不重复拉
SEARCH_LIMIT = 6          # search 模式最多取前 N 个
LOOKUP_CACHE_KEEP = 24 * 3600   # 查询词缓存：过期条目也留着（熔断时拿它秒回，24h 一清）

# ---- ① 熔断器（2026-09-15 真机：Clash 的翻墙通道会整段断掉）----
# 实测断网时：走代理 百度 ✅ 0.15s / Google ❌ Connection reset 4.81s / CoinGecko ❌ 4.82s
# → 代理收下连接、出口隧道死了；面板却要每个查询干等 12 秒预算烧完才回 0 条。
# 连续**连接类**失败够了就**闸掉上游**：熔断期间秒回缓存 / 秒回「代理不可用」，别再让用户干等。
# 2026-09-15 纠偏（用户报「查询失败」的直接原因）：
#   · HTTP 429 是**上游额度**，不是网断 —— 单独记进 _CB_429，**不计**入网络失败次数
#     （否则一次限流就把整个查询锁死，用户看到的就是「怎么查都失败」）
#   · 5xx 同理：代理通、上游挂了，也不该当成「代理断」的证据
#   · 只有连接类错误（URLError / timeout / reset / EOF）才计入 fails
#   · 熔断**分源**：CoinGecko 一个、DexScreener 一个，coinpaprika 等各归各的桶
#     （CoinGecko 挂了绝不能连累 DexScreener —— 地址查询靠它兜底）
CB_FAIL_THRESHOLD = 4     # 连续**连接类**失败这么多次 → 打开熔断
CB_OPEN_S = 25.0          # 熔断 25 秒；到点进半开，只放**一个**请求出去试
CB_SOURCES = ("coingecko", "dexscreener", "coinpaprika", "defillama",
              "geckoterminal", "geckoterminal_search")
#   分源熔断的源清单：GeckoTerminal 的逐链兜底 / /search/pools 主源**各一个桶**（互不连累）
CB_PANEL_SOURCES = ("coingecko", "dexscreener")   # 面板那行小字只显示这两个（任务要求）
CB_SOURCE_LABEL = {"coingecko": "CoinGecko", "dexscreener": "DexScreener",
                   "coinpaprika": "CoinPaprika", "defillama": "DefiLlama",
                   "geckoterminal": "GeckoTerminal",
                   "geckoterminal_search": "GeckoTerminal /search/pools"}

# ---- 改动①（2026-09-16 真机）：GT 的限流状态必须**自愈**（半开探测 + 有界冷却，绝不永久） ----
#   真机事故：手工直查 GT 0.97s 就答了，面板却是 symbol=None +「GeckoTerminal 限流中（HTTP 429），
#   未能自证…symbol 因限流暂缺」；**重启面板**后同一个查询立刻恢复 → 卡在面板内存里的状态。
#   现在 GT 的两个桶（geckoterminal / geckoterminal_search）统一成「有界冷却 + 半开探测」：
#     · 触发（429 / 连不上 / 自证里的 rate 结论）→ 冷却 GT_COOLDOWN_SEC（默认 60s，env 可覆盖）
#     · 冷却到期 → **必须**放行 1 个请求去探路（半开）；成功 → 状态全清、恢复全速
#     · 探路还是失败 → 冷却翻倍（60 → 120 → …），但**封顶 GT_COOLDOWN_MAX_SEC（10 分钟）**
#     · 每个冷却到期都再给一次机会；探路名额另有**租约**（GT_PROBE_LEASE_SEC 一过就作废）
#       → **不存在「必须重启进程才能恢复」的状态**
#   任何 ≥15 分钟的封闭都只能由「连续 N 次半开探测失败」驱动，并在 blocked_by / 日志里写明
#   「已探测 N 次仍失败，下次尝试时间 HH:MM:SS」。
#   ⛔ GMGN 那套按报文解封时间恢复的逻辑不在这里（costbasis 那侧），两套互不影响。
GT_RATE_SOURCES = ("geckoterminal", "geckoterminal_search")
GT_COOLDOWN_SEC = _env_float("GT_COOLDOWN_SEC", 60.0)          # 首次触发的冷却
GT_COOLDOWN_MAX_SEC = _env_float("GT_COOLDOWN_MAX_SEC", 600.0)  # 上限 10 分钟（翻倍也只到这里）
GT_COOLDOWN_MAX_SEC = max(GT_COOLDOWN_SEC, min(GT_COOLDOWN_MAX_SEC, 600.0))
# 探路名额的**租约**：拿走了却半天不还（拿不回结论）→ 自动作废、下一次到期照旧给一个 ——
# 这是「绝不出现必须重启才能恢复的状态」的安全阀（正常一枪 GT 请求远快于这个数）。
GT_PROBE_LEASE_SEC = _env_float("GT_PROBE_LEASE_SEC", 30.0)
GT_PROBE_LONG_CLOSED_SEC = 15 * 60.0                           # ≥15 分钟的封闭必须写明探路次数

# ---- ③ 本地符号索引：查词少打一次 /search，429 概率直接减半 ----
GECKO_LIST_TTL = 12 * 3600      # /coins/list（~1.5MB）内存缓存 12 小时（⛔ 绝不落盘）
GECKO_LIST_FAIL_TTL = 300       # 拉失败后 5 分钟内不重复拉那 1.5MB

# ---- ② DefiLlama 直连行情：唯一不依赖代理的兜底源 ----
# 实测 2026-09-15：coins.llama.fi **本机直连可达**（0.84s），走代理反而被隧道拖死。
LLAMA_PRICE_BATCH = 50    # /prices/current 一批最多 50 个 gecko id
# ⚠️ 真机：代理死的时候，前面几次重试会把 12 秒预算烧光 —— 这时候如果还共用那个 deadline，
#    直连的兜底会被「超出查询预算」直接掐掉（明明它 0.84 秒就能回）。所以给它**自己的**小预算。
LLAMA_PRICE_BUDGET = 3.0

# ---- 连接复用 ----
# 每个域名维持一条经代理 CONNECT 隧道的长连接；闲置超过这么久就主动丢掉重建
CONN_IDLE_MAX = 60.0

URL_GECKO_SEARCH = "https://api.coingecko.com/api/v3/search?query=%s"
URL_GECKO_MARKETS = ("https://api.coingecko.com/api/v3/coins/markets"
                     "?vs_currency=usd&ids=%s&price_change_percentage=7d,30d,1y")
URL_GECKO_CONTRACT = "https://api.coingecko.com/api/v3/coins/%s/contract/%s"
# ---- GeckoTerminal 按地址查池子：**地址查询的主源**（全链可用）----
# 2026-09-15 真机（经代理 1.5s）：BSC meme ✓18 池 / BSC CAKE ✓20 池 $2.2924 / ETH PEPE ✓ / SOL BONK ✓
#   字段（真机核对过，别猜）：data[].attributes.name（"PEPE / USDC 1%" = base / quote）、
#   address（池地址）、base_token_price_usd、reserve_in_usd（流动性）、fdv_usd、market_cap_usd、
#   pool_created_at（ISO 8601，如 "2023-04-17T18:43:35Z"）、volume_usd.h24、price_change_percentage.h24
URL_GT_TOKEN_POOLS = ("https://api.geckoterminal.com/api/v2/networks/%s/tokens/%s/pools?page=1")
# 代币对象本身（2026-09-16 真机：/search/pools 只给 relationships.base_token.data.id，**不给 symbol**）：
#   attributes.symbol（"BNC4"）/ name / decimals …——「用户给的是池子」时靠这一发把 symbol 换成
#   **基础代币自己的** symbol（绝不拿池名 "BNC4 / 4Stock" 的任一侧冒充代币符号）。
URL_GT_TOKEN = "https://api.geckoterminal.com/api/v2/networks/%s/tokens/%s"
# **改动①指定的那一枪**：单个池子对象（relationships.base_token / quote_token 的权威来源）。
# 与 /search/pools 命中的同一条池子是**同一份** relationships → 平时不用打（省额度）；
# 只有 /search/pools 那条**读不出** base/quote 时才补这一发（真机字段同 /search/pools 的 data[0]）。
URL_GT_POOL = "https://api.geckoterminal.com/api/v2/networks/%s/pools/%s"
# ---- GeckoTerminal /search/pools：**一次调用定链**（2026-09-15 真机验证）----
# 逐链探测 = **7 次请求/查询**，而 GT 免费额度约 30 次/分 → 1 分钟只够 ~4 次地址查询，
# bsc 排第 3 个经常已经 429（真机实测：BSC meme 因 bsc 429 而 0 条）。这个端点一次就够：
# **链名就藏在 data[].id 的 "<network>_<address>" 前缀里**（如 "bsc_0x0ed7e529…"）。
# 真机实测（2026-09-15 22:2x）：
#   BSC 0x0eD7e52944161450477ee417DE9Cd3a859b14fD0 → ✓1 条 "Cake / WBNB" 流动性 $19.86M
#   SOL BONK DezXAZ8z… → ✓20 条 "Bonk / SOL" $277K；名字 cake → ✓20 条（顺带支持池子名搜索）
# 字段（真机核对过，**别猜**）：data[].id（"<network>_<池地址>"）/ attributes.address（池地址）/
#   name("base / quote") / base_token_price_usd / reserve_in_usd(流动性) / fdv_usd / market_cap_usd /
#   pool_created_at(ISO 8601) / volume_usd.h24 / price_change_percentage.h24 /
#   transactions.h24{buys,sells,buyers,sellers} /
#   relationships.base_token.data.id("<net>_<代币地址>") / quote_token.data.id / dex.data.id
# ⚠️ 关键语义：base_token 的地址 == 查询地址 → 查的是**代币**；不等（如查的是 LP 池地址）→
#    查的是**池子** —— 必须如实标注 kind，绝不把池子当币、也不把币当池子。
# ⚠️ 2026-09-16 真机事故（用户给的就是池子地址 0x4682c6bf…）：查询地址 == **池子自己的 id 地址**、
#    且 base_token 是**另一个**地址 → 判定 kind="pool_address"，把该行的 token_address 换成
#    base_token 地址（kind 记 "token"）+ notes 写明「你给的是池子 …，已按基础代币 … 分析」。
#    池子地址照旧留在 pair_address（前端「池子地址」那行照样显示），取数一律用代币地址。
# ⚠️ 沙箱无网：上面这两条是**真机记录**，联调/回归都靠离线打桩（test_blood_chips_web.py）；
#    端点的真实连通性/额度表现**需 host 真机验证**（真机一次调用 ≈1.5s）。
URL_GT_SEARCH_POOLS = "https://api.geckoterminal.com/api/v2/search/pools?query=%s"
GT_SEARCH_SOURCE = "geckoterminal /search/pools（一次定链）"
GT_SEARCH_LIMIT = 5           # 每个查询最多出这么多行（按 reserve_in_usd 降序）
# 哨兵：_gt_search_rows(pools=…) —— 调用方（_do_address 的池子身份前置判定）**已经**拿过这一枪时
# 直接把结果传进来复用（同一件事绝不发两次请求）；默认值 = 还没查过 → 函数自己发。
_GT_POOLS_UNFETCHED = object()
# 0x 地址逐链试（任务指定的顺序）；base58 输入先试 solana
GT_EVM_NETS = ("eth", "base", "bsc", "arbitrum", "polygon", "optimism", "gnosis")
GT_SOL_NETS = ("solana",)
GT_NET_LABELS = {"eth": "以太坊", "base": "Base", "bsc": "BSC 币安智能链",
                 "arbitrum": "Arbitrum", "polygon": "Polygon", "optimism": "Optimism",
                 "gnosis": "Gnosis", "solana": "Solana"}
# GT 的 network id → CoinGecko platform（只用于**补** mcap/距ATH；对不上就不补，绝不猜）
GT_NET_GECKO_PLATFORM = {"eth": "ethereum", "base": "base", "bsc": "binance-smart-chain",
                         "arbitrum": "arbitrum-one", "polygon": "polygon-pos",
                         "optimism": "optimistic-ethereum", "gnosis": "xdai",
                         "solana": "solana"}
GT_POOL_MIN_INTERVAL = 0.25   # 逐链探测时相邻请求至少隔这么久（并行也要限流：免费额度约 30 次/分）
GT_PROBE_WORKERS = 4          # 并行探测 7 条链的线程数：**别开满 7**，免得自己把 GT 打进 429
GT_NEG_TTL = 300.0            # 某链「探过、没有这个地址的池子」→ 负缓存 5 分钟（连点不重复打）
# ---- 改动⑤（2026-09-16 真机）：**自证结论**按 (链, 池子) 缓存在内存里，别把额度烧在重复自证上 ----
#   同一 (链, 池子) 的自证结论 10 分钟内不再重问（含「真没有池子」这种失败结论）；
#   但结论是建在 rate / down 上的（降级用了 base_token）只缓存 60 秒 —— 限流一过立刻重问。
#   ⛔ 只放内存，绝不落盘（和 _GT_NEG_CACHE / _STRUCT_CACHE 同一条铁律）。
GT_SELF_TTL = 600.0           # 拿到了确定回答（自证通过 / 404 / 200 空）→ 缓存 10 分钟
GT_SELF_RATE_TTL = 60.0       # 结论建在 rate / down 上 → 只缓存 60 秒（限流恢复后要能重问）
# 自证这一枪的结论分类（_gt_token_pools 的 kind；**429 ≠ 空**，这是改动④的核心）：
#   ok       非空 → 自证通过
#   nodata   HTTP 200 但空 = **确定回答**「真没有池子」
#   notfound HTTP 404 = **确定回答**（GT 没收录这个代币）
#   rate     429 / 本地节流 / 熔断 = **不确定**，绝不当成「没有池子」
#   down     超时 / 5xx / 连不上 = **不确定**，同上
GT_SELF_BLOCKED_KINDS = ("rate", "down")      # 「没能自证」≠「没有池子」的那两类
GT_SELF_EMPTY_KINDS = ("nodata", "notfound")  # 「拿到了确定回答：真没有池子」的那两类

# 地址查询备用源（2026-09-15 真机：代理把它路由到死节点 → 0.6s 秒断；但仍留着当备用）
URL_DEXSCREEN_TOKEN = "https://api.dexscreener.com/latest/dex/tokens/%s"
URL_LLAMA = "https://api.llama.fi/protocols"
# ⛔ 这一个**必须直连**（见 _DIRECT_OPENER）：代理整段断掉时它是唯一还活着的行情源
URL_LLAMA_PRICES = "https://coins.llama.fi/prices/current/%s"
URL_GECKO_LIST = "https://api.coingecko.com/api/v3/coins/list"   # 全量 id/symbol/name（~1.5MB）
# ---- CoinPaprika：名称搜索的**第二源**（CoinGecko 自己失败 / 无结果才问它；免费版精度较低）----
# 真机实测（2026-09-15，经代理 0.6s）：cake→CAKE/PancakeSwap(rank91) ✓  pepe→PEPE/Pepe(rank54) ✓
#                                     币安→币安人生(rank116) ✓（**中文可搜**）
# limit=10（多要几条候选，排序/截断交给 order_search_coins 按 rank 升序）；c=currencies 只要币
URL_PAPRIKA_SEARCH = "https://api.coinpaprika.com/v1/search?q=%s&c=currencies&limit=10"
URL_PAPRIKA_TICKER = "https://api.coinpaprika.com/v1/tickers/%s"
# ⚠️ 2026-09-15 真机事故：CoinGecko 的 id（polkadot）和 CoinPaprika 的 id
#    （dot-polkadot-token）**不是一回事**。检索成功、行情失败时，拿 CoinGecko 的 id 去
#    逐个 `/tickers/<id>` 必然空手 → 降级触发了但价格仍是 null。
#    正路是**全量端点**：一次拉 2000 个币，再按 symbol 对号（实测 ~1.6MB / 1.7s）。
URL_PAPRIKA_BULK = "https://api.coinpaprika.com/v1/tickers"
PAPRIKA_BULK_TTL = 600      # 全量快照内存缓存 10 分钟（⛔ 只放内存、绝不落盘）
# 合约地址按这个顺序试，命中即停
CHAIN_PLATFORMS = ("ethereum", "binance-smart-chain", "solana", "base", "arbitrum-one")

# 四段串行：发现层 → 观察池 → 折价区 → 深度层
# （同一个线程里依次跑，中途失败也继续跑完，最后把第一个失败码暴露出去）
REFRESH_STAGES = [
    ("发现层", ["python3", "blood_chips_discover.py", "--once"]),
    ("观察池", ["python3", "blood_chips_monitor.py", "--once", "--market", "crypto"]),
    ("折价区", ["python3", "blood_chips_discount.py", "--once"]),
    ("深度层", ["python3", "blood_chips_deep.py", "--once"]),
]
REFRESH_CMDS = [cmd for _label, cmd in REFRESH_STAGES]


# ------------------------------------------------------------------ 取值工具

def _num(v):
    """数值 → float / None。bool 不算数值；数字字符串尝试解析；NaN/Inf 丢弃。"""
    if isinstance(v, bool) or v is None:
        return None
    if not isinstance(v, (int, float)):
        s = str(v).strip().replace(",", "").replace("%", "")
        if not s:
            return None
        try:
            v = float(s)
        except (TypeError, ValueError):
            return None
    v = float(v)
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return int(v) if v.is_integer() and abs(v) < 1e15 else v


def _int(v):
    n = _num(v)
    if n is None:
        return None
    try:
        return int(n)
    except (TypeError, ValueError, OverflowError):
        return None


def _str(v):
    return v if isinstance(v, str) and v.strip() else None


def _dict(v):
    return v if isinstance(v, dict) else {}


def _list(v):
    return v if isinstance(v, list) else []


def _ratio(v):
    """广度/分位这类「占比」：文件里可能是 63.6（百分数）也可能是 0.636（比例）。
    统一归一化成 0..1 比例（前端 ×100 显示），越界值原样保留。"""
    n = _num(v)
    if n is None:
        return None
    if abs(n) > 1.0:
        return round(n / 100.0, 6)
    return n


def _neg(v):
    """回撤统一按负方向输出（源文件里有正有负）。"""
    n = _num(v)
    if n is None:
        return None
    return -abs(n)


def _load(path):
    """读 JSON → (obj, ok)。任何异常都不抛，返回 (None, False)。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
        if not isinstance(obj, (dict, list)):
            return None, False
        return obj, True
    except Exception:
        return None, False


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------- /api/lookup
# ⚠️ 交互路径的两个「并行」要点（2026-09-16 起）：
#   ① 结构取数（第 1 条的 struct_rr）**不是**行情富化跑完才起步的：它在行情富化之前就进线程池，
#      行情侧一确定第 1 条的身份（_publish_struct_row → _StructSlot.put）就开跑；
#   ② 两份预算各算各的 —— 行情 = LOOKUP_BUDGET / ADDRESS_LOOKUP_BUDGET，结构 = STRUCT_BUDGET_SEC。
#   ③ 结构那份 deadline **从交接点起算**（t_handoff + STRUCT_BUDGET_SEC）：行情侧再慢，结构也有
#      自己的一整份预算（老口径从请求开始算 → 交接晚到 10+ 秒时一动手就秒超；交接始终没来则用
#      请求开始 + LOOKUP_TOTAL_TIMEOUT_SEC 兜底）。四个时刻进 struct_rr.debug.timings。
#      行情把预算吃光只是行情那半截缺字段，**绝不许**让结构盈亏比一次请求都没发就放弃。
#      整体上限 = max(两份) + LOOKUP_TOTAL_MARGIN_SEC(2s)。

class _BufferedResponse(object):
    """已经读完 body 的响应 → 给 _http_json 的最小接口（getcode / read / 上下文管理）。"""

    def __init__(self, status, body):
        self._status = status
        self._body = body

    def getcode(self):
        return self._status

    def read(self, *_a):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _PooledHTTPSOpener(object):
    """按域名复用一条经代理 CONNECT 隧道的 HTTPS 长连接（替代每次 open() 都新建 TCP+TLS）。

    为什么：urllib 每次 opener.open() 都是全新 TCP + TLS 握手，经 Clash 代理做 CONNECT
    隧道时，快速连续查询会撞上 `_ssl.c:999 handshake timed out` / `UNEXPECTED_EOF`；
    实测连查 4 个词之后 /coins/markets 就开始握手超时 → 整段行情 null → 面板「全是 —」。
    复用同一条 keep-alive 连接省掉握手，单次请求 ≈0.3–0.5s，握手类失败也大幅减少。

    规矩：
      · 每个域名一把锁：同一条连接绝不被两个线程同时用
      · 任何异常 / 超时 / 对端关闭（sock is None）→ 丢弃该连接，下次请求重新握手
      · 复用到的连接第一次就失败（代理已经悄悄关掉的死连接）→ 立刻换新连接再试一次
      · 闲置超过 CONN_IDLE_MAX 秒 → 主动丢弃，不走死连接

    注意：接口故意做成和 urllib 的 opener 一样（`open(req, timeout=…)`），
    这样 _http_json 的重试逻辑一行都不用改，测试里替换 _OPENER 也照样有效。
    """

    def __init__(self, proxy):
        parts = urllib.parse.urlsplit(proxy if "://" in proxy else "http://" + proxy)
        self.proxy_host = parts.hostname or "127.0.0.1"
        self.proxy_port = parts.port or 7890
        self._lock = threading.Lock()      # 只保护下面两张表
        self._locks = {}                   # (scheme, host, port) -> Lock（每域名一把）
        self._conns = {}                   # (scheme, host, port) -> (conn, 最近一次用的 monotonic)

    # ---- 连接池 ----
    def _host_lock(self, key):
        with self._lock:
            lk = self._locks.get(key)
            if lk is None:
                lk = self._locks[key] = threading.Lock()
            return lk

    def _take(self, key):
        """取出可复用的连接；闲置太久 / 已被对端关掉的一律丢弃。"""
        with self._lock:
            cur = self._conns.get(key)
        if cur is None:
            return None
        conn, ts = cur
        if (time.monotonic() - ts) > CONN_IDLE_MAX or getattr(conn, "sock", None) is None:
            self._drop(key, conn)
            return None
        return conn

    def _put(self, key, conn):
        with self._lock:
            self._conns[key] = (conn, time.monotonic())

    def _drop(self, key, conn):
        with self._lock:
            cur = self._conns.get(key)
            if cur is not None and cur[0] is conn:
                self._conns.pop(key, None)
        try:
            conn.close()
        except Exception:
            pass

    def _new_conn(self, scheme, host, port, timeout):
        if scheme == "https":
            conn = http.client.HTTPSConnection(self.proxy_host, self.proxy_port, timeout=timeout)
            conn.set_tunnel(host, port)    # 让代理先 CONNECT 到目标域名再 TLS
        else:
            conn = http.client.HTTPConnection(self.proxy_host, self.proxy_port, timeout=timeout)
        return conn

    # ---- 和 urllib opener 同款接口 ----
    def open(self, req, timeout=None):
        url = req.full_url
        parts = urllib.parse.urlsplit(url)
        scheme = (parts.scheme or "https").lower()
        host = parts.hostname
        if not host:
            raise ValueError("URL 缺少主机名: %s" % url)
        port = parts.port or (443 if scheme == "https" else 80)
        key = (scheme, host, port)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        timeout = HTTP_TIMEOUT if timeout is None else timeout
        headers = dict(getattr(req, "headers", None) or {})
        method = req.get_method() if hasattr(req, "get_method") else "GET"

        with self._host_lock(key):
            conn = self._take(key)
            reused = conn is not None
            if conn is None:
                conn = self._new_conn(scheme, host, port, timeout)
            while True:
                try:
                    if conn.sock is not None and timeout:
                        conn.sock.settimeout(timeout)   # 复用连接要按本次预算重设超时
                    # 明文 HTTP 走代理用绝对 URI；HTTPS 隧道用 path
                    conn.request(method, url if scheme == "http" else path, headers=headers)
                    resp = conn.getresponse()
                    status, reason, rheaders = resp.status, resp.reason, resp.headers
                    body = resp.read()
                    try:
                        resp.close()
                    except Exception:
                        pass
                except Exception:
                    self._drop(key, conn)
                    if reused:      # 复用到的死连接：换新连接立刻再试一次，别浪费一次重试
                        reused = False
                        conn = self._new_conn(scheme, host, port, timeout)
                        continue
                    raise
                break

        # 对端说了 Connection: close → http.client 已经关掉 sock，不能复用
        if getattr(conn, "sock", None) is None:
            self._drop(key, conn)
        else:
            self._put(key, conn)
        if status >= 400:       # 保持 urllib 的语义：4xx/5xx 抛 HTTPError（429 才能退避）
            raise urllib.error.HTTPError(url, status, reason or "", rheaders, io.BytesIO(body))
        return _BufferedResponse(status, body)


class _ParallelOpener(object):
    """并行探测专用的小连接池：内部是 K 个**各自独立**的 _PooledHTTPSOpener。

    为什么不能直接共用 _OPENER：它对**同一个域名只有一条长连接 + 一把锁**
    （`with self._host_lock(key)` 罩住整个请求）→ 同一个 host 的 7 条链并行探测会被它又串成串行。
    这里放 K 份独立的池子：单份内部仍然「同域名一条 keep-alive 连接」（省掉重复握手），
    K 份合起来 = 最多 K 条连接同时在飞（K = GT_PROBE_WORKERS = 4）。
    借出 / 归还走队列：没空闲就等 —— 等价于把并发压在 K，接口和 urllib opener 一样。
    """

    def __init__(self, proxy, size):
        self.size = max(1, int(size))
        self._free = queue.Queue()
        for _ in range(self.size):
            self._free.put(_PooledHTTPSOpener(proxy))

    def open(self, req, timeout=None):
        opener = self._free.get()
        try:
            return opener.open(req, timeout=timeout)
        finally:
            self._free.put(opener)


_OPENER = _PooledHTTPSOpener(PROXY)

# ⛔ 直连专用 opener（DefiLlama 行情兜底）：显式清空 ProxyHandler，
#    连环境变量里的 http_proxy/https_proxy 都不看 —— 「直连」就得是真直连。
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# GeckoTerminal 并行探测专用：K 份独立连接池，7 条链才真能同时在飞（见 _ParallelOpener）
_GT_OPENER = _ParallelOpener(PROXY, GT_PROBE_WORKERS)

_GECKO_LOCK = threading.Lock()
_GECKO_LAST = [0.0]


def _gecko_slot():
    """CoinGecko 全局串行：拿锁 → 睡够间隔 → 记发车时间。所有 CoinGecko 请求都从这里取号。"""
    with _GECKO_LOCK:
        wait = GECKO_MIN_INTERVAL - (time.monotonic() - _GECKO_LAST[0])
        if wait > 0:
            time.sleep(wait)
        _GECKO_LAST[0] = time.monotonic()


def _retry_wait(code):
    """失败分类：只有 429 值得退避 3 秒；连接类（code is None）瞬间失败，立刻重试。
    其他 HTTP 状态码（403/404/5xx…）不重试。返回 None = 不重试。"""
    if code == 429:
        return WAIT_RETRY_429
    if code is None:
        return WAIT_RETRY_FAST
    return None


def _retry_after_sec(headers):
    """上游 429 响应里的 Retry-After → 秒（只认数字形式）。

    GeckoTerminal 限流时会带这个头 → 调用方按**它**退避（比瞎猜 3 秒准）。
    ⚠️ 拿不到 / 不是数字 / 负数 → None（**绝不编一个数**：调用方自己有默认退避值）。
    ⚠️ 沙箱无网：这条路径的真实报文**需在 host 真机验证**（离线用例只喂构造的头）。"""
    if headers is None:
        return None
    try:
        raw = headers.get("Retry-After")
    except Exception:                                           # noqa: BLE001 - 头读不出来就当没有
        return None
    val = _num(raw)
    return val if (val is not None and val >= 0) else None


# ---- ① 熔断器：代理整段断掉时「秒回实话」，而不是每个查询干等 12 秒 ----
# 连续**连接类**失败 ≥ CB_FAIL_THRESHOLD → 打开 CB_OPEN_S 秒：熔断期间 build_lookup 立即
# 返回（零上游请求）；有缓存吃缓存，没缓存就写清原因。
# 冷却结束 = 半开：只放**一个**请求出去试；成功 → 立刻清零并关闭；失败 → 再开 CB_OPEN_S 秒。
# ⚠️ 分源：每个上游一个桶，CoinGecko 挂了绝不连累 DexScreener（地址查询靠它兜底）。
_CB_LOCK = threading.Lock()


def _cb_blank():
    return {"fails": 0, "open_until": 0.0, "probe": False, "probe_at": 0.0,
            "n429": 0, "last429": 0.0,
            # ↓ 改动①：GT 统一冷却用（别的源不用，留着也不碍事）
            "probe_fails": 0, "cooldown_s": 0.0, "blocked_since": 0.0,
            "last_block": 0.0, "why": None}


_CB = {src: _cb_blank() for src in CB_SOURCES}


def _cb_is_gt(source):
    """这个熔断桶是不是 GeckoTerminal 的（GT 走「有界冷却 + 半开探测」，见文件头的常量注释）。"""
    return source in GT_RATE_SOURCES


def _gt_cooldown_s(probe_fails):
    """半开探测已经失败 N 次 → 这次冷却多少秒：GT_COOLDOWN_SEC × 2^N，**封顶 10 分钟**。

    60 → 120 → 240 → 480 → 600（封顶）；**绝不无限增长、更不会变成永久**。"""
    n = max(0, _int(probe_fails) or 0)
    try:
        cd = GT_COOLDOWN_SEC * float(2 ** min(n, 16))
    except (OverflowError, ValueError):                          # 理论到不了，兜底也一样有界
        cd = GT_COOLDOWN_MAX_SEC
    return min(float(cd), GT_COOLDOWN_MAX_SEC)


def _gt_next_try_hhmm(open_until, now=None):
    """冷却到点的**本机时刻**「HH:MM:SS」（面板文案 / 日志用）；算不出 → 空串（绝不编一个时间）。"""
    left = (_num(open_until) or 0.0) - (time.monotonic() if now is None else now)
    if left <= 0:
        return ""
    try:
        return time.strftime("%H:%M:%S", time.localtime(time.time() + left))
    except Exception:                                            # noqa: BLE001 - 时间格式化失败就当拿不到
        return ""


def _cb_probe_live(st, now):
    """半开探路名额还在飞吗（调用方持锁）？租约（GT_PROBE_LEASE_SEC）一过就当它作废 ——
    一个拿不回结论的探路**绝不能**把状态永久卡住（真机事故就是这个方向的坑）。"""
    if not st["probe"]:
        return False
    at = _num(st.get("probe_at")) or 0.0
    if at and (now - at) > GT_PROBE_LEASE_SEC:
        st["probe"] = False
        st["probe_at"] = 0.0
        return False
    return True


def _cb_blocked_locked(st, now):
    """这个桶**当前**是不是「别碰它」：冷却窗口内，或半开名额还在飞（调用方持锁）。"""
    if st["open_until"] > now:
        return True
    return bool(st["open_until"]) and _cb_probe_live(st, now)


def _gt_cool_note(source, st, now):
    """GT 冷却中的人话（面板 / 日志同一口径）：说清冷却多久、还剩多久、探了几次、下次什么时候试。

    「已探测 N 次仍失败，下次尝试时间 HH:MM:SS」是任务口径 —— N 拿不准就整句不给（绝不编）。"""
    label = CB_SOURCE_LABEL.get(source, _str(source) or "GeckoTerminal")
    cd = int(st.get("cooldown_s") or GT_COOLDOWN_SEC)
    left = max(1, int((st["open_until"] - now) + 0.999))
    n = _int(st.get("probe_fails")) or 0
    hhmm = _gt_next_try_hhmm(st["open_until"], now)
    head = ("%s 限流中（HTTP 429）" if (_str(st.get("why")) or "rate") != "down"
            else "%s 没答话（超时 / 5xx / 连不上）") % label
    out = "%s：冷却 %d 秒（还剩 %d 秒）" % (head, cd, left)
    if n and hhmm:
        out += "；已探测 %d 次仍失败，下次尝试时间 %s" % (n, hhmm)
    held = (now - (_num(st.get("blocked_since")) or 0.0)) if st.get("blocked_since") else 0.0
    if held >= GT_PROBE_LONG_CLOSED_SEC:
        # ≥15 分钟的封闭**只可能**由「连续 N 次半开探测失败」造成（冷却有上限、每次到期都给机会）
        out += "（已连续封闭 %.0f 分钟，每次到点都探测过、共失败 %d 次）" % (held / 60.0, n)
    return out


def _gt_engage_locked(source, st, now, kind="rate"):
    """GT 的一次失败（429 / 连不上）→ 触发或**升级**统一冷却（调用方持锁）。

    · 冷却窗口内又收到失败（并发那几发）→ 冷却**不延长**（保持有界）
    · 冷却**已过期**的那一发 = 「半开探测的结论是失败」→ 探路失败 +1 → 冷却翻倍（封顶 10 分钟）
    · 探路名额还在飞时失败 → 也算探路失败（顺手把名额释放掉）
    · 完全没在冷却里（首次触发）→ 从 GT_COOLDOWN_SEC 起算
    无论哪条路，出口都留下一个**到点必放一个探路请求**的 open_until —— 没有永久态。"""
    if st["open_until"] > now:          # 冷却窗口内又收到失败 → 冷却不延长（保持有界）
        st["probe"] = False
        st["probe_at"] = 0.0
        st["last_block"] = now
        return None
    if st["open_until"] or _cb_probe_live(st, now):
        st["probe_fails"] = (_int(st.get("probe_fails")) or 0) + 1   # 半开探测失败 → 翻倍
    else:
        st["probe_fails"] = 0
    cd = _gt_cooldown_s(st["probe_fails"])
    st["open_until"] = now + cd
    st["cooldown_s"] = cd
    st["probe"] = False
    st["probe_at"] = 0.0
    st["why"] = "down" if _str(kind) == "down" else "rate"
    if not st["blocked_since"]:
        st["blocked_since"] = now
    st["last_block"] = now
    return _gt_cool_note(source, st, now)      # 交给调用方去写日志（别在 _CB_LOCK 里动别的锁）


def _cb_state(source="coingecko"):
    """取某源的熔断桶（调用方持锁；未知源惰性建桶，**不共享** CoinGecko 的计数）。"""
    key = source if source in _CB else (_str(source) or "coingecko")
    st = _CB.get(key)
    if st is None:
        st = _CB[key] = _cb_blank()
    return st


def _cb_msg(source="coingecko"):
    """熔断期间给用户看的那句话（哪个源、什么错、多久恢复；面板原样显示，不编数字）。"""
    if _cb_is_gt(source):
        with _CB_LOCK:
            st = _cb_state(source)
            now = time.monotonic()
            if st["open_until"] > now:
                return _gt_cool_note(source, st, now)
            cd = int(st.get("cooldown_s") or GT_COOLDOWN_SEC)
        label = CB_SOURCE_LABEL.get(source, _str(source) or "GeckoTerminal")
        return ("%s 限流中：冷却 %d 秒（封顶 %d 秒），到点自动再探一次、成功即恢复全速"
                % (label, cd, int(GT_COOLDOWN_MAX_SEC)))
    label = CB_SOURCE_LABEL.get(source, _str(source) or "上游")
    return ("网络代理不可用：%s 连续 %d 次请求失败（连接类错误），已熔断 %d 秒、恢复后自动重试；"
            "代理翻墙隧道不通请换 Clash 节点"
            % (label, CB_FAIL_THRESHOLD, int(CB_OPEN_S)))


def _cb_reset(source=None):
    """手工复位（测试用）。不传 → 清空所有源。"""
    with _CB_LOCK:
        for key in list(_CB):
            _CB[key] = _cb_blank()
        if source is not None and source not in _CB:
            _CB[source] = _cb_blank()


def _cb_fails(source="coingecko"):
    """某源连续连接类失败次数。"""
    with _CB_LOCK:
        return _cb_state(source)["fails"]


def _cb_429(source="coingecko"):
    """某源累计 429（限流）次数 —— 单独计数，**不**算网络失败。"""
    with _CB_LOCK:
        return _cb_state(source)["n429"]


def _cb_is_open(source="coingecko"):
    """要不要**完全不碰这个源**（build_lookup 立刻吃缓存 / 说实话）。True = 正在熔断。
    冷却到点后（半开）返回 False —— 让这一次查询去探路；但如果已经有别的线程在探路，
    就仍然按「熔断中」处理：别让一堆查询同时去撞已经死掉的上游。
    改动①：探路名额有租约 —— 悬着的探路一到点就作废、立刻又能探，**状态绝不永久卡死**。"""
    with _CB_LOCK:
        return _cb_blocked_locked(_cb_state(source), time.monotonic())


def _cb_gate(source="coingecko"):
    """请求闸门 → (allowed, note)。allowed=False 表示「这个源正在熔断」，别发请求。
    冷却到点后自动进半开：只放第一个请求，其余的等这次探路有结论。
    改动①：GT 的冷却到期**一定**会放一个名额（且名额本身有租约）→ 不存在永久封闭。"""
    with _CB_LOCK:
        st = _cb_state(source)
        now = time.monotonic()
        left = st["open_until"] - now
        if left > 0:
            if _cb_is_gt(source):
                return False, _gt_cool_note(source, st, now)
            return False, "%s（还剩 %d 秒）" % (_cb_msg(source), max(1, int(left + 0.999)))
        if st["open_until"]:
            if _cb_probe_live(st, now):
                return False, "熔断半开：探路请求还在飞，先不重复发"
            st["probe"] = True
            st["probe_at"] = now
            return True, None
        return True, None


def _cb_note_ok(source="coingecko"):
    """一次请求成功 → 清零并关闭这个源的熔断（半开探路成功也走这里）。
    改动①：GT 恢复 → 连同 `_gt_self_*` 里那些 rate / down 结论一起丢掉
    （限流一过，symbol 立刻能重新自证 —— 「必须重启进程」的另一半就是这个缓存）。"""
    with _CB_LOCK:
        _cb_state(source).update(fails=0, open_until=0.0, probe=False, probe_at=0.0,
                                 probe_fails=0, cooldown_s=0.0, blocked_since=0.0, why=None)
    if _cb_is_gt(source):
        _gt_self_drop_blocked()


def _cb_note_fail(source="coingecko"):
    """一次连接类失败 → 计数；连续够了就（重新）熔断。
    非 GT：老口径（CB_FAIL_THRESHOLD 次 → CB_OPEN_S 秒，到点半开）。
    GT：走统一的有界冷却 —— 探路失败 → 冷却翻倍、封顶 10 分钟，到点照旧再给一次机会。"""
    now = time.monotonic()
    note = None
    with _CB_LOCK:
        st = _cb_state(source)
        st["fails"] += 1
        if not _cb_is_gt(source):
            if st["fails"] >= CB_FAIL_THRESHOLD:
                st["open_until"] = now + CB_OPEN_S
                st["probe"] = False
                st["probe_at"] = 0.0
            return
        # 冷却里又失败 / 冷却已过期（= 半开探测的结论是失败）/ 连续失败够多 → 统一升级
        if st["open_until"] or st["fails"] >= CB_FAIL_THRESHOLD:
            note = _gt_engage_locked(source, st, now, "down")
    if note:
        _log_line("熔断（GT）：" + note)


def _cb_note_429(source="coingecko"):
    """一次 429 → 只累加限流计数，**绝不**碰 fails（429 是上游额度，不是网断）。
    改动①：GT 的 429 **直接触发统一冷却**（有界 + 半开）—— 真机那个「重启才能恢复」的状态
    就是这里：以前 429 除了计数什么都不做，别的路径写下的封闭态就再没人清。"""
    now = time.monotonic()
    note = None
    with _CB_LOCK:
        st = _cb_state(source)
        st["n429"] += 1
        st["last429"] = now
        if _cb_is_gt(source):
            note = _gt_engage_locked(source, st, now, "rate")
    if note:
        _log_line("熔断（GT）：" + note)


def _cb_probe_release(source="coingecko"):
    """HTTP 层答了话（429 / 4xx / 5xx 都算「代理通」）→ 释放半开探路名额：
    只在「冷却已过、正在半开」时清空计数并关闸；**熔断窗口内**不动
    （免得窗口里冒出一个 429 就把熔断提前关掉）。
    改动①：GT 的 429 已经由 `_cb_note_429` 记成「统一冷却」→ 这里**绝不清它**（清了就等于自己
    把刚记下的限流状态抹掉）；GT 只在冷却已过期时收尾（半开结论 = 上游答了话 → 清零）。"""
    now = time.monotonic()
    with _CB_LOCK:
        st = _cb_state(source)
        if _cb_is_gt(source) and st["open_until"] > now:
            st["probe"] = False
            st["probe_at"] = 0.0
            return
        if st["open_until"] and st["open_until"] <= now:
            st.update(fails=0, open_until=0.0, probe=False, probe_at=0.0,
                      probe_fails=0, cooldown_s=0.0, blocked_since=0.0)
        elif _cb_is_gt(source):
            st["probe"] = False
            st["probe_at"] = 0.0


def _gt_rate_recovered(source="geckoterminal"):
    """GT 的统一冷却**曾经**生效、现在已过期 / 已被半开成功清掉（= 状态该翻篇了）。

    改动①：用来把「限流中」那些**历史结论**（自证缓存里的 rate / down）一并作废 ——
    冷却一过就必须重新问一次，不许拿旧结论顶着。从没封闭过 → False（老口径一个字不变）。"""
    with _CB_LOCK:
        st = _cb_state(source)
        return bool(st["last_block"]) and st["open_until"] <= time.monotonic()


def _gt_cooling(source="geckoterminal"):
    """GT 这个桶**现在**是不是正在冷却窗口里（改动③：blocked_by 要不要出现就问它）。"""
    with _CB_LOCK:
        return _cb_state(source)["open_until"] > time.monotonic()


def _gt_entry_keep(source="geckoterminal"):
    """GT 的结构化 blocked_by 条目**现在**还要不要露出来（改动③：必须是「当前状态」）。

    · 统一冷却正在生效（或半开探路还在飞）→ 留（这就是当前状态）
    · 统一冷却**曾经**生效过、现在已过期 / 已被半开成功清掉 → **丢**（不许再挂历史状态）
    · 这个进程从没记过 GT 冷却（结论来自别的腿，如 `_gt_json` 的 429）→ 照旧照实带上（老口径不变）"""
    with _CB_LOCK:
        st = _cb_state(source)
        if _cb_blocked_locked(st, time.monotonic()):
            return True
        return not st["last_block"]


def _gt_entry_extra(source="geckoterminal"):
    """GT 冷却**还在**时给 blocked_by 补的诊断字段（改动①：≥15 分钟的封闭必须写明探路次数）。

    → {"probe_fails": N, "next_try_in_s": 秒, "next_try_hhmm": "HH:MM:SS", "note": 人话}
    冷却已过期 / 从没冷却过 → {}（一个字段都不加，绝不编）。"""
    with _CB_LOCK:
        st = _cb_state(source)
        now = time.monotonic()
        if st["open_until"] <= now:
            return {}
        left = max(1, int((st["open_until"] - now) + 0.999))
        hhmm = _gt_next_try_hhmm(st["open_until"], now)
        n = _int(st.get("probe_fails")) or 0
    held = (now - (_num(st.get("blocked_since")) or 0.0)) if st.get("blocked_since") else 0.0
    out = {"next_try_in_s": left}
    if hhmm:
        out["next_try_hhmm"] = hhmm
    if n:
        out["probe_fails"] = n
        if hhmm:
            out["note"] = "已探测 %d 次仍失败，下次尝试时间 %s" % (n, hhmm)
    if held >= GT_PROBE_LONG_CLOSED_SEC:
        # 长时间封闭必须留证据：是**探测一直失败**（不是「必须重启」那种死状态）
        out["closed_for_s"] = int(round(held))
    return out


def _ra_out(val):
    """结构化字段里的 retry_after_s：**>0 才给数字**；0 / 负数 / 拿不到 → None。

    改动③：面板上再也不许出现 `retry_after_s: 0`（那不是「恢复时间」，是噪声）。"""
    v = _num(val)
    return v if (v is not None and v > 0) else None


def _cb_settle(ok, code=None, source="coingecko"):
    """一次 _http_json 的结论记账（分源）。ok=False 时：
    · code is None → 连不上（URLError / timeout / reset / EOF）→ 这就是「代理断了」的证据，计 1 次失败
    · code == 429 → 上游额度（限流）→ 只记 _CB_429，**不计**网络失败（否则一限流就锁死整个查询）
    · 其他 4xx / 5xx → 请求本身是通的（上游答了话）→ 也不算「代理断了」
      （否则随便粘一个不存在的合约地址，连试 5 条链就能把熔断打开）"""
    if ok:
        _cb_note_ok(source)
        return
    if code == 429:
        _cb_note_429(source)
        # 半开时收到 429 = 代理通 → 释放探路名额。⚠️ GT：`_cb_note_429` 刚记下的统一冷却
        # **绝不能被这里清掉**（见 `_cb_probe_release` 里那条 GT 分支）；GMGN 之外的别的源口径不变。
        _cb_probe_release(source)
        return
    if code is None:
        _cb_note_fail(source)
        return
    _cb_probe_release(source)


def _cb_panel():
    """面板上那行小字：上游:CoinGecko(熔断中 12s) DexScreener(正常)。"""
    now = time.monotonic()
    parts = []
    with _CB_LOCK:
        for src in CB_PANEL_SOURCES:
            st = _cb_state(src)
            label = CB_SOURCE_LABEL.get(src, src)
            left = st["open_until"] - now
            if left > 0:
                parts.append("%s(熔断中 %ds)" % (label, max(1, int(left + 0.999))))
            elif st["open_until"]:
                parts.append("%s(半开探测)" % label)
            else:
                parts.append("%s(正常)" % label)
    return "上游:" + " ".join(parts)


def _http_json(url, throttled=True, deadline=None, opener=None, breaker=True,
               source="coingecko", budget=None, retry_429=None, meta=None):
    """GET + 解析 JSON，返回 (data, err, code, rate_limited)，任何失败都不抛异常。
    429 → **只再试 RETRY_MAX_429 次**（分钟级窗口，硬重试纯属烧预算）并给 rate_limited=True，
    调用方据此改走「陈旧缓存 → CoinPaprika 降级」；
    连接类失败（code is None）→ WAIT_RETRY_FAST 快重试；其他状态码不重试。
    deadline（time.monotonic() 时刻）到了就立刻收手、不再重试，
    调用方把缺的字段写成「缺失」，绝不让用户干等。
    code 是 HTTP 状态码（连接类失败为 None），调用方靠它区分「404 未收录」和真失败。

    opener  默认 _OPENER（走代理）；传 _DIRECT_OPENER 就是本机直连（DefiLlama 兜底专用）。
    breaker=True（默认）参与**这个源**的熔断记账 + 受**这个源**的熔断闸门限制；
    source 指定熔断桶（coingecko / geckoterminal / dexscreener / …），绝不会串源。
    budget 只影响「超出查询预算 Ns」这句文案里的 N（地址查询 15s / 名称搜索 12s）。
    retry_429=False → **429 一枪就交回调用方**（不在这里睡 3 秒）：GeckoTerminal 的调用方
    （_gt_json）要按上游的 Retry-After 自己退避，且退避必须**记进 GT 自己的子预算**。
    meta（可选 dict）→ 出口写回 {"code","rate_limited","retry_after_s"}：让调用方能拿到
    上游 429 报文里的 Retry-After（秒；没有就是 None），退避文案/结构化字段都靠它。"""
    budget = LOOKUP_BUDGET if budget is None else budget
    if breaker:
        allowed, why = _cb_gate(source)
        if not allowed:
            return None, why, None, False
    opener = _OPENER if opener is None else opener
    headers = {"User-Agent": UA_CHROME, "Accept": "application/json"}
    err, code = "未知错误", None
    ok = False
    retry_after_s = None
    try:
        for attempt in range(RETRY_MAX + 1):
            timeout = HTTP_TIMEOUT
            if deadline is not None:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None, "超出查询预算 %ss" % budget, None, False
                # 单次超时也得压在预算内，否则一个卡住的连接就能把 12 秒预算拖成 20 秒
                timeout = min(HTTP_TIMEOUT, left)
            if throttled:
                _gecko_slot()
            try:
                req = urllib.request.Request(url, headers=headers)
                with opener.open(req, timeout=timeout) as resp:
                    code = resp.getcode()
                    raw = resp.read()
            except Exception as exc:          # 单个源失败不能中断整轮
                code = getattr(exc, "code", None)
                reason = getattr(exc, "reason", None)
                if code is not None:
                    err = "HTTP %s %s" % (code, reason)
                else:
                    err = "%s: %s" % (type(exc).__name__, exc)
                if code == 429:
                    retry_after_s = _retry_after_sec(getattr(exc, "headers", None))
                    if retry_429 is False:
                        # 调用方（GT）自己按 Retry-After 退避重试 → 这一枪原样交回，不在这里睡
                        return None, err, code, True
                wait = _retry_wait(code)
                # 429 不值得硬重试（同一分钟窗口里必然还是 429）→ 只再试 1 次
                max_retry = RETRY_MAX_429 if code == 429 else RETRY_MAX
                if wait is not None and attempt < max_retry:
                    if deadline is not None:
                        wait = min(wait, max(0.0, deadline - time.monotonic()))
                        if wait <= 0:
                            return None, "超出查询预算 %ss（%s）" % (budget, err), None, False
                    time.sleep(wait)
                    continue
                return None, err, code, code == 429
            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                return None, "JSON 解析失败: %s" % exc, code, code == 429
            ok = True
            return data, None, code, False
        return None, err, code, code == 429
    finally:
        if meta is not None:        # 任何出口都把这一枪的结论写回（GT 的退避靠它读 Retry-After）
            meta["code"] = code
            meta["rate_limited"] = bool(code == 429)
            meta["retry_after_s"] = _num(retry_after_s)
        if breaker:      # 任何出口都要给熔断器一个结论（半开的探路名额必须被释放）
            _cb_settle(ok, code, source)


_LLAMA_LOCK = threading.Lock()
_LLAMA_CACHE = {"ts": 0.0, "idx": None, "err": None}
_LLAMA_BUSY = [False]           # 是否已有后台线程在拉索引


def _llama_index():
    """（后台线程专用）DefiLlama /protocols → {gecko_id: {tvl, category, chains}}。
    构建失败返回 (None, err)。**不要放在 /api/lookup 的交互路径上**：那是一次 ~8.8MB 拉取。
    拉取/构建都不持 _LLAMA_LOCK，所以交互路径查缓存时不会被这里堵住。"""
    data, err, _code, _rl = _http_json(URL_LLAMA, throttled=False,
                                           source="defillama")  # 不是 CoinGecko，不占它的限速
    if not isinstance(data, list):
        with _LLAMA_LOCK:
            _LLAMA_CACHE.update(ts=time.time(), idx=None, err=err or "返回格式不是列表")
            return None, _LLAMA_CACHE["err"]
    idx = {}
    for p in data:
        if not isinstance(p, dict):
            continue
        raw_gid = p.get("gecko_id")
        gid = raw_gid.strip() if isinstance(raw_gid, str) else ""
        if not gid:
            continue
        tvl = _num(p.get("tvl"))
        cur = idx.get(gid)
        if cur is None or (tvl is not None and (cur["tvl"] is None or tvl > cur["tvl"])):
            chains = p.get("chains")
            idx[gid] = {"tvl": tvl, "category": _str(p.get("category")),
                        "chains": chains if isinstance(chains, list) else None}
    with _LLAMA_LOCK:
        _LLAMA_CACHE.update(ts=time.time(), idx=idx, err=None)
    return idx, None


def _llama_index_ready():
    """交互路径专用：只读缓存，绝不阻塞。返回 (idx, note)，idx 为 None 表示暂时没索引
    （note 说明原因，并已挂后台刷新）—— 调用方把 TVL / 类别 / 链 记「缺失」继续往下走。"""
    with _LLAMA_LOCK:
        age = time.time() - _LLAMA_CACHE["ts"]
        idx, err, busy = _LLAMA_CACHE["idx"], _LLAMA_CACHE["err"], _LLAMA_BUSY[0]
    if idx is not None and age < LLAMA_TTL:
        return idx, None
    if err and age < LLAMA_FAIL_TTL:
        return None, err                      # 刚失败过，别重复拉那 8.8MB
    if busy:
        return None, "索引后台刷新中"
    _llama_refresh_async()
    if idx is not None:
        return idx, None                      # 旧索引也先用着，后台顺手刷新
    return None, "索引未缓存，后台刷新中"


def _llama_refresh_async():
    """挂一个后台线程去拉索引；已经有线程在跑就不重复挂。"""
    with _LLAMA_LOCK:
        if _LLAMA_BUSY[0]:
            return
        _LLAMA_BUSY[0] = True
    threading.Thread(target=_llama_refresh_worker, name="llama-index", daemon=True).start()


def _llama_refresh_worker():
    try:
        _llama_index()
    except Exception as exc:                  # 后台线程的异常不能漏出去
        with _LLAMA_LOCK:
            _LLAMA_CACHE.update(ts=time.time(), idx=None,
                                err="%s: %s" % (type(exc).__name__, exc))
    finally:
        with _LLAMA_LOCK:
            _LLAMA_BUSY[0] = False


# ---- ③ 本地符号索引（/coins/list 全量 id/symbol/name）----
# 每次查询都先打一次 /search 是最容易被 429 的地方：有了本地索引，正常查询从 2 请求降到 1 请求。
# ⛔ 只放内存（~1.5MB），**一个文件都不落盘**；拉不到 / 熔断开 → 静默跳过，老实用 /search。
_GECKO_LIST_LOCK = threading.Lock()
#   ts      = 这份 rows 是什么时候拉到的（决定 12 小时的新鲜度）
#   fail_ts = 最近一次拉失败的时刻（决定 5 分钟内不再重拉；失败**不清掉旧的 rows**）
_GECKO_LIST = {"ts": 0.0, "fail_ts": 0.0, "rows": None, "by_sym": {}, "err": None}
_GECKO_LIST_BUSY = [False]        # 是否已有后台线程在拉


def _gecko_list_parse(data):
    """/coins/list 原始 list → (rows, by_sym)。坏行直接跳过，绝不抛异常。"""
    rows, by_sym = [], {}
    for raw in _list(data):
        coin = _dict(raw)
        cid = _str(coin.get("id"))
        cid = cid.strip() if cid else ""
        if not cid:
            continue
        row = {"id": cid, "symbol": coin.get("symbol"), "name": _str(coin.get("name"))}
        rows.append(row)
        sym = _sym(row["symbol"])
        if sym:
            by_sym.setdefault(sym, []).append(row)
    return rows, by_sym


def _gecko_list_index():
    """（后台线程专用）拉一次 /coins/list（~1.5MB）建内存索引。失败返回 None，绝不抛异常。"""
    data, err, _code, _rl = _http_json(URL_GECKO_LIST, source="coingecko")
    if not isinstance(data, list) or not data:
        with _GECKO_LIST_LOCK:
            # ⚠️ 已经有索引就**别扔**（过期的索引也比没有强：至少省掉一次 /search）；
            #    失败只记 fail_ts，不打乱那份 rows 的新鲜度（否则一失败就等于续命 12 小时）。
            _GECKO_LIST.update(fail_ts=time.time(), err=err or "返回格式不是列表")
        return None
    rows, by_sym = _gecko_list_parse(data)
    with _GECKO_LIST_LOCK:
        _GECKO_LIST.update(ts=time.time(), fail_ts=0.0, rows=rows, by_sym=by_sym, err=None)
    return rows


def _gecko_list_refresh_async():
    """挂后台线程去拉索引；已经有线程在跑就不重复挂。"""
    with _GECKO_LIST_LOCK:
        if _GECKO_LIST_BUSY[0]:
            return
        _GECKO_LIST_BUSY[0] = True
    threading.Thread(target=_gecko_list_refresh_worker, name="gecko-list", daemon=True).start()


def _gecko_list_refresh_worker():
    try:
        _gecko_list_index()
    except Exception as exc:                  # 后台线程的异常不能漏出去
        with _GECKO_LIST_LOCK:
            _GECKO_LIST.update(fail_ts=time.time(),
                               err="%s: %s" % (type(exc).__name__, exc))
    finally:
        with _GECKO_LIST_LOCK:
            _GECKO_LIST_BUSY[0] = False


def _gecko_list_ready(allow_fetch=True):
    """交互路径专用：只读内存索引，绝不阻塞 → (rows, by_sym) / None（没索引 = 老实用 /search）。
    allow_fetch=False（熔断期）连后台线程都不挂：熔断期间不许发任何上游请求。"""
    now = time.time()
    with _GECKO_LIST_LOCK:
        age = now - _GECKO_LIST["ts"]
        fail_age = now - _GECKO_LIST["fail_ts"]
        rows, by_sym = _GECKO_LIST["rows"], _GECKO_LIST["by_sym"]
        busy = _GECKO_LIST_BUSY[0]
    if rows is not None and age < GECKO_LIST_TTL:
        return rows, by_sym                     # 新鲜命中
    if fail_age < GECKO_LIST_FAIL_TTL:
        return (rows, by_sym) if rows is not None else None   # 刚失败过：别把 1.5MB 反复拉
    if allow_fetch and not busy:
        _gecko_list_refresh_async()
    return (rows, by_sym) if rows is not None else None   # 过期也先用着，后台顺手刷新


def _local_index_match(q, allow_fetch=True):
    """查询词 → 形如 /search 的 coins 列表；索引没就绪 / 没命中 → None（调用方回落 /search）。
    匹配顺序（需求指定）：symbol 精确 → name 精确 → name 子串。"""
    idx = _gecko_list_ready(allow_fetch)
    if idx is None:
        return None
    rows, by_sym = idx
    s = (q or "").strip()
    if not s:
        return None
    low = s.lower()
    hits = list(by_sym.get(s.upper()) or ())
    if not hits:
        hits = [r for r in rows if (r.get("name") or "").lower() == low]
    if not hits:
        hits = [r for r in rows if low in (r.get("name") or "").lower()]
    if not hits:
        return None
    return hits[:SEARCH_LIMIT * 4]      # 真正排序 / 去重 / 截断交给 order_search_coins


# ---- 判定 / 排序 / 打分（纯函数，可单测） ----

_MODE_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")          # EVM 合约
_MODE_SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")  # Solana base58 地址


def detect_mode(q):
    """去空白后像合约地址 → address，否则 search。"""
    s = (q or "").strip()
    if _MODE_EVM_RE.match(s) or _MODE_SOL_RE.match(s):
        return "address"
    return "search"


def _sym(v):
    """符号统一大写（与 blood_chips_discover.py 的 build_row 一致，方便和候选池对比）。"""
    s = _str(v)
    return s.upper() if s else None


# ---- DexScreener 地址查询（主源）：chainId → 人话 / → CoinGecko platform ----
# ⚠️ 不认识的 chainId **原样显示**，绝不硬编一个名字上去（见 _ds_chain_name）。
DS_CHAIN_NAMES = {
    "bsc": "BSC 币安智能链",
    "ethereum": "以太坊",
    "solana": "Solana",
    "base": "Base",
    "arbitrum": "Arbitrum",
    "polygon": "Polygon",
    "optimism": "Optimism",
    "gnosis": "Gnosis",
    "avalanche": "Avalanche",
    "fantom": "Fantom",
    "tron": "Tron",
    "sui": "Sui",
}
# DexScreener chainId → CoinGecko platform（只用于**补** mcap/距ATH；对不上就不补，绝不猜）
DS_CHAIN_GECKO_PLATFORM = {
    "ethereum": "ethereum",
    "bsc": "binance-smart-chain",
    "solana": "solana",
    "base": "base",
    "arbitrum": "arbitrum-one",
    "polygon": "polygon-pos",
    "optimism": "optimistic-ethereum",
    "gnosis": "xdai",
}
DS_TOKEN_SOURCE = "dexscreener /latest/dex/tokens（不限链）"


def _ds_chain_name(chain_id):
    """chainId → 人话（bsc → BSC 币安智能链）；不认识的**原样返回**，不硬编。"""
    cid = _str(chain_id)
    if not cid:
        return None
    return DS_CHAIN_NAMES.get(cid.strip().lower(), cid)


def _pct_any(obj, key):
    """CoinGecko 的百分比字段两套命名都兼容：
    /coins/markets  → price_change_percentage_7d_in_currency（数值）
    market_data     → price_change_percentage_7d（数值）或 _in_currency（{usd:…} 字典）"""
    v = _num(obj.get(key + "_in_currency"))
    if v is not None:
        return v
    v = _num(obj.get(key))
    if v is not None:
        return v
    return _num(_dict(obj.get(key + "_in_currency")).get("usd"))


def _coin_sort_key(coin, q):
    sym = coin.get("symbol")
    sym = sym.strip().lower() if isinstance(sym, str) else ""
    exact = 0 if (sym and sym == (q or "").strip().lower()) else 1
    rank = _num(coin.get("market_cap_rank"))
    if rank is None:                    # CoinPaprika /search 用的是 rank（CoinGecko 是 market_cap_rank）
        rank = _num(coin.get("rank"))
    if rank is not None and rank <= 0:  # CoinPaprika 用 rank 0 表示「未排名」→ 排最后，不是排第一
        rank = None
    return (exact, 0 if rank is not None else 1, rank if rank is not None else 0.0)


def order_search_coins(coins, q):
    """symbol 与 q 完全相等（不分大小写）优先 → market_cap_rank 升序（None 垫底）
    → 去重 id → 取前 SEARCH_LIMIT。sort 稳定，同档保持数据源原序。
    同时服务 CoinGecko /search 和 CoinPaprika /search（后者只有 rank、还有 is_active）。"""
    rows = [c for c in coins if isinstance(c, dict) and c.get("is_active") is not False]
    rows.sort(key=lambda c: _coin_sort_key(c, q))
    seen, out = set(), []
    for c in rows:
        cid = _str(c.get("id"))
        cid = cid.strip() if cid else ""
        if not cid or cid in seen:
            continue
        seen.add(cid)
        out.append(c)
    return out[:SEARCH_LIMIT]


def _cs_score(row):
    """错杀分 —— 与 blood_chips_discover.py 的 _cs_score 是同一套公式（改一处必须改两处）：
    = 0.45 * 回撤深度分 + 0.35 * 市值分 + 0.20 * 使用度分
      · 回撤深度分 = |距ATH%| / 90，封顶 1.0（跌超 90% 视为同一回事，避免「接近归零」霸榜）
      · 市值分     = log10(市值) 在 $1亿 ~ $1000亿 之间归一，封顶 0..1
      · 使用度分   = 有 TVL 时 min(TVL/市值, 1.0)；无 TVL 时 min((24h量/市值) / 0.2, 1.0)
    """
    dd = row.get("drawdown_from_ath_pct")
    dd_s = min(abs(dd) / 90.0, 1.0) if dd is not None else 0.0
    mc = row.get("mcap_usd")
    mc_s = 0.0
    if mc and mc > 0:
        mc_s = max(0.0, min((math.log10(mc) - 8.0) / 3.0, 1.0))
    us_s = 0.0
    tvl_r = row.get("tvl_mcap_ratio")
    vol_r = row.get("vol_mcap_ratio")
    if tvl_r is not None and tvl_r > 0:
        us_s = min(tvl_r, 1.0)
    elif vol_r is not None and vol_r > 0:
        us_s = min(vol_r / 0.2, 1.0)
    return round(0.45 * dd_s + 0.35 * mc_s + 0.20 * us_s, 4)


# ---- 结果行 ----

RESULT_FIELDS = ("id", "symbol", "name", "market_cap_rank", "price_usd",
                 "drawdown_from_ath_pct", "ret_7d_pct", "ret_30d_pct", "ret_1y_pct",
                 "mcap_usd", "vol24h_usd", "tvl_usd", "tvl_mcap_ratio",
                 "category", "chains", "screen_score", "in_candidates", "in_watchlist",
                 "source", "source_age_s",
                 # ---- 地址查询专属字段：DexScreener / GeckoTerminal 给的真字段才有值 ----
                 # chain：结果行**自己**带的链标识（GT network id / DexScreener chainId / costbasis 链名）；
                 #        和 chain_id 同值，单列出来是给下游「按链 + 合约地址取日线」用（GMGN/GT 都要链名）
                 "chain_id", "chain_name", "chain", "dex_id", "address", "pair_address",
                 "liquidity_usd", "fdv", "volume_24h", "price_change_24h",
                 "pair_created_at", "pool_created_at",
                 # kind：查的到底是代币还是池子（token / quote_token / lp_pool / pool_hit，
                 # 判不出来就 null + 原因）；token_address：代表「查询地址本人」的那个代币
                 "kind", "token_address",
                 # notes：这一行**必须原样告诉用户**的口径注释（如「你给的是池子 0x…，
                 #        已按基础代币 0x… 分析」）；没有就 null
                 "notes",
                 "null_fields")
#   source:       **行情**的实际来源 —— coingecko（含新鲜缓存命中）/ coinpaprika（检索也降级了）
#                 / mixed（身份来自 CoinGecko、数值来自 CoinPaprika）/ cache（吃了过期缓存）
#   source_age_s: 行情来自缓存时的数据年龄（秒）；走网络取到时为 null（前端显示「N 分钟前」）


def _base_from_market(row):
    """/coins/markets 的一行 → 事实字段（缺的留 None）。"""
    return {
        "id": _str(row.get("id")),
        "symbol": _sym(row.get("symbol")),
        "name": _str(row.get("name")),
        "market_cap_rank": _int(row.get("market_cap_rank")),
        "price_usd": _num(row.get("current_price")),
        "drawdown_from_ath_pct": _neg(row.get("ath_change_percentage")),
        "ret_7d_pct": _pct_any(row, "price_change_percentage_7d"),
        "ret_30d_pct": _pct_any(row, "price_change_percentage_30d"),
        "ret_1y_pct": _pct_any(row, "price_change_percentage_1y"),
        "mcap_usd": _num(row.get("market_cap")),
        "vol24h_usd": _num(row.get("total_volume")),
    }


def _base_from_search(coin):
    """行情明细拿不到时的降级：只留 /search 给得到的字段，其余 null。"""
    return {
        "id": _str(coin.get("id")),
        "symbol": _sym(coin.get("symbol")),
        "name": _str(coin.get("name")),
        "market_cap_rank": _int(coin.get("market_cap_rank")),
        "price_usd": None,
        "drawdown_from_ath_pct": None,
        "ret_7d_pct": None,
        "ret_30d_pct": None,
        "ret_1y_pct": None,
        "mcap_usd": None,
        "vol24h_usd": None,
    }


def _base_from_coin(coin):
    """/coins/{platform}/contract/{addr} 的 coin 对象 → 事实字段。"""
    md = _dict(coin.get("market_data"))
    rank = _int(coin.get("market_cap_rank"))
    if rank is None:
        rank = _int(md.get("market_cap_rank"))
    return {
        "id": _str(coin.get("id")),
        "symbol": _sym(coin.get("symbol")),
        "name": _str(coin.get("name")),
        "market_cap_rank": rank,
        "price_usd": _num(_dict(md.get("current_price")).get("usd")),
        "drawdown_from_ath_pct": _neg(_dict(md.get("ath_change_percentage")).get("usd")),
        "ret_7d_pct": _pct_any(md, "price_change_percentage_7d"),
        "ret_30d_pct": _pct_any(md, "price_change_percentage_30d"),
        "ret_1y_pct": _pct_any(md, "price_change_percentage_1y"),
        "mcap_usd": _num(_dict(md.get("market_cap")).get("usd")),
        "vol24h_usd": _num(_dict(md.get("total_volume")).get("usd")),
    }


def _paprika_pct(v):
    """CoinPaprika 免费版对 7d/30d/1y 常直接给 0（其实是「没这个数据」）→ 视为缺失写 null。
    ⚠️ 绝不把 0 当事实写进结果，否则面板上会出现「7d 0.0%」这种假数据。"""
    n = _num(v)
    if n is None or n == 0:
        return None
    return n


def _base_from_paprika(coin, ticker):
    """CoinPaprika /search 一行 +(可选)/tickers/<id> → 事实字段（口径对齐 CoinGecko，缺的留 None）。
    ⚠️ 免费版精度低：percent_from_price_ath 是两位小数粗估；7d/30d/1y 常缺；TVL/类别/链它没有。"""
    t = _dict(ticker)
    usd = _dict(_dict(t.get("quotes")).get("USD"))
    rank = _int(t.get("rank"))
    if rank is None:
        rank = _int(coin.get("rank"))
    if rank is not None and rank <= 0:      # CoinPaprika 用 rank 0 表示「未排名」
        rank = None
    return {
        "id": _str(t.get("id")) or _str(coin.get("id")),
        "symbol": _sym(t.get("symbol")) or _sym(coin.get("symbol")),
        "name": _str(t.get("name")) or _str(coin.get("name")),
        "market_cap_rank": rank,
        "price_usd": _num(usd.get("price")),
        "drawdown_from_ath_pct": _neg(usd.get("percent_from_price_ath")),
        "ret_7d_pct": _paprika_pct(usd.get("percent_change_7d")),
        "ret_30d_pct": _paprika_pct(usd.get("percent_change_30d")),
        "ret_1y_pct": _paprika_pct(usd.get("percent_change_1y")),
        "mcap_usd": _num(usd.get("market_cap")),
        "vol24h_usd": _num(usd.get("volume_24h")),
    }


def _base_from_bulk(coin, rec):
    """CoinPaprika 全量快照的一条 + CoinGecko 的身份 → 事实字段（口径对齐 CoinGecko）。
    身份（id/symbol/名称）用 CoinGecko 的，数值才来自 CoinPaprika（→ source="mixed"，
    候选池/观察池靠 gecko id 仍能对上）。缺的留 None，绝不编。
    ⚠️ 免费版：percent_from_price_ath 是负数、两位小数（-65.71）；
       7d/30d/1y 常缺或直接给 0（= 没有这个数据）→ null；TVL/类别/链它没有 → null。"""
    usd = _paprika_usd(rec)
    rank = _int(_dict(rec).get("rank"))
    if rank is None or rank <= 0:           # CoinPaprika 用 rank 0 表示「未排名」
        rank = _int(_dict(coin).get("market_cap_rank"))
    if rank is not None and rank <= 0:
        rank = None
    return {
        "id": _str(_dict(coin).get("id")),
        "symbol": _sym(_dict(coin).get("symbol")) or _sym(_dict(rec).get("symbol")),
        "name": _str(_dict(coin).get("name")) or _str(_dict(rec).get("name")),
        "market_cap_rank": rank,
        "price_usd": _num(usd.get("price")),
        "drawdown_from_ath_pct": _neg(usd.get("percent_from_price_ath")),
        "ret_7d_pct": _paprika_pct(usd.get("percent_change_7d")),
        "ret_30d_pct": _paprika_pct(usd.get("percent_change_30d")),
        "ret_1y_pct": _paprika_pct(usd.get("percent_change_1y")),
        "mcap_usd": _num(usd.get("market_cap")),
        "vol24h_usd": _num(usd.get("volume_24h")),
    }


# 「这行到底有没有行情数字」：一个都没有就不能标 coingecko（那是谎报数据来源，见 source="none"）。
# 只看真行情字段，不含 market_cap_rank —— 排名从 /search 也能拿到，不代表有行情。
MARKET_VALUE_FIELDS = ("price_usd", "mcap_usd", "vol24h_usd", "drawdown_from_ath_pct",
                       "ret_7d_pct", "ret_30d_pct", "ret_1y_pct")


def _has_market_values(base):
    base = _dict(base)
    return any(base.get(k) is not None for k in MARKET_VALUE_FIELDS)


def _result_row(base, tvl_rec, source="coingecko", age_s=None):
    """补 TVL / 派生比率 / 错杀分，再按 RESULT_FIELDS 固定字段顺序输出（多余的一律丢掉）。
    in_candidates / in_watchlist 先给 false，由 build_lookup 每次现算覆盖。"""
    mcap = base.get("mcap_usd")
    vol = base.get("vol24h_usd")
    tvl = tvl_rec.get("tvl") if isinstance(tvl_rec, dict) else None
    base["tvl_usd"] = tvl
    base["tvl_mcap_ratio"] = (tvl / mcap) if (tvl is not None and mcap) else None
    base["vol_mcap_ratio"] = (vol / mcap) if (vol is not None and mcap) else None   # 只给算分用
    base["category"] = tvl_rec.get("category") if isinstance(tvl_rec, dict) else None
    base["chains"] = tvl_rec.get("chains") if isinstance(tvl_rec, dict) else None
    base["screen_score"] = _cs_score(base)
    base.pop("vol_mcap_ratio", None)
    base["in_candidates"] = False
    base["in_watchlist"] = False
    base["source"] = source
    base["source_age_s"] = None if age_s is None else int(round(float(age_s)))
    return {k: base.get(k) for k in RESULT_FIELDS}


def _candidate_ids():
    """只有 candidates[] 里 listed 的算 true（passed 计数里但没列出的不算）。"""
    obj, _ok = _load(CANDIDATES_JSON)
    out = set()
    for r in _list(_dict(obj).get("candidates")):
        cid = _str(_dict(r).get("id"))
        if cid:
            out.add(cid.strip())
    return out


def _watchlist_ids():
    obj, _ok = _load(WATCHLIST_JSON)
    out = set()
    for w in _list(_dict(obj).get("crypto")):
        cid = _str(_dict(w).get("id"))
        if cid:
            out.add(cid.strip())
    return out


def _stamp_pool_flags(rows, cand_ids, watch_ids):
    """池子标记每次现算：结果缓存 10 分钟，但「刚加进观察池」必须立刻反映出来。"""
    out = []
    for r in rows:
        row = dict(r)
        cid = _str(row.get("id"))
        cid = cid.strip() if cid else ""
        row["in_candidates"] = bool(cid and cid in cand_ids)
        row["in_watchlist"] = bool(cid and cid in watch_ids)
        out.append(row)
    return out



# ---- 按「币 id」的行情缓存（两层缓存的第一层；进程重启不丢） ----
# 原来只按**查询词**缓存 _LOOKUP_CACHE：`apt` / `aptos` / `aptos-labs` 命中同一个币，
# 却各打一次 /coins/markets —— 连查几个词就把 CoinGecko 的分钟级额度打爆（429）。
# 现在一次请求拿到 N 个币就按 id 拆成 N 条存下来，换词命中同一个币时**零请求**。
# 过期条目也留着当兜底（429 时拿「N 分钟前」的数据，也比面板全是「—」强）。
MARKET_CACHE_KEEP = 7 * 24 * 3600   # 过期条目最多留 7 天，再老的直接丢
MARKET_CACHE_MAX = 800              # 落盘最多留这么多条（新的优先），防止文件无限长
MARKET_CACHE_JSON = os.path.join(DATA_DIR, "blood_chips_market_cache.json")

# ⛔ 用户明确要求（2026-09-15）：「千万千万不要在我磁盘里建立库」。
#    → 行情缓存**只放内存**，一个文件都不落。进程重启 = 缓存清空（重新拉一遍即可）。
#    下面 _market_load_locked / _market_save_locked 里的落盘逻辑**保留但被这个开关关掉**，
#    以后用户要开再改 True。MARKET_CACHE_JSON 已不再被读写。
MARKET_CACHE_PERSIST = False

# ---------------------------------------------------------------- 带血筹码（成本分布）接入
# 面板复用 blood_chips_costbasis 的分析：**同一进程最多 1 个任务在跑**，多的排队；
# 结果只放内存（_COSTBASIS_CACHE，TTL 15 分钟），⛔ 绝不落盘（和上面 MARKET_CACHE_PERSIST 同一条铁律）。
COSTBASIS_TTL = 900.0            # 结果内存缓存 15 分钟（过期即丢，绝不写文件）
COSTBASIS_HOLDERS = 20           # 默认分析前 N 大地址（costbasis 的默认值也是 20）
COSTBASIS_POLL_S = 3.0           # 前端轮询间隔（秒）——只写在这里给前端对齐
# /api/costbasis/resolve 的预算（秒）：**它现在先跑完整套面板查询排序**（build_lookup 自己就有
# LOOKUP_BUDGET 12s：本地索引 → CoinGecko /search → CoinPaprika → GeckoTerminal），再退
# GeckoTerminal /search/pools（真机 ~1.5s）、最后才试 CoinGecko platforms → 12s 装不下，
# 25s 才够「一条路失败还有下一条」。解析不出照样立刻说清原因（前端先显示解析结果再起任务）。
COSTBASIS_BUDGET = 25.0
COSTBASIS_JOB_KEEP = 40          # 内存里最多留几个任务
# ---- /api/costbasis/resolve 的**地址**解析预算（秒）----
# 真机事故（2026-09-15）：`GET /api/costbasis/resolve?q=<0x 地址>` 实测 **41.66s** ——
# 0x 地址先走 DexScreener，重试把预算吃光，前端把「还在跑」误判成「服务死了」。
# 现在地址这条路（0x / base58）：
#   ① GMGN market search -q <地址>：**1 次请求**就给 chain/address/liquidity，命中即用
#   ② 没命中 → DexScreener 兜底，但 HardCap ≤ RESOLVE_DEX_BUDGET（绝不再拖到 40s+）
#   ③ 全程压在 RESOLVE_BUDGET 内；超预算 → ok:false + 写明**试过哪些源、各什么错**
# 正确性不牺牲：宁可 12 秒内明确 ok:false，也不要 40 秒无响应（也绝不瞎猜链）。
# ⚠️ 币名那条路（q 不是地址）照旧用 COSTBASIS_BUDGET(25s)：它要跑 build_lookup（自己就 12s）
#    → GeckoTerminal /search/pools → CoinGecko platforms，12s 装不下。
RESOLVE_BUDGET = 12.0            # 地址解析总预算（秒）
RESOLVE_GMGN_BUDGET = 8.0        # 其中留给 GMGN market search 的上限（gmgn-cli 自身超时 30s，这里硬卡）
RESOLVE_DEX_BUDGET = 6.0         # 其中留给 DexScreener 兜底的上限（重试预算压到 ≤6 秒）

# CoinGecko platform → 本工具支持的链（只认 CHAIN_INFO 里有的；bsc 等一律不支持，不猜）
GECKO_PLATFORM_CHAIN = {
    "ethereum": "ethereum", "eth": "ethereum",
    "base": "base",
    "arbitrum-one": "arbitrum", "arbitrum": "arbitrum",
    "polygon-pos": "polygon", "polygon": "polygon",
    "optimistic-ethereum": "optimism", "optimism": "optimism",
    "xdai": "gnosis", "gnosis": "gnosis",
    "solana": "solana",
}
# 选链优先级（任务书原文顺序）：ethereum > base > arbitrum > polygon > optimism > gnosis > solana
COSTBASIS_CHAIN_PRIORITY = ("ethereum", "base", "arbitrum", "polygon",
                            "optimism", "gnosis", "solana")
URL_GECKO_COIN = ("https://api.coingecko.com/api/v3/coins/%s"
                  "?localization=false&tickers=false&market_data=false"
                  "&community_data=false&developer_data=false")

_MARKET_LOCK = threading.Lock()
_MARKET_CACHE = {}          # key -> {"ts": epoch, "base": {行情字段}, "origin": 源}
_MARKET_LOADED = [False]    # 第一次用到才读盘（文件不在 / 写坏了 → 当空，绝不抛异常）


def _market_key(cid, origin="coingecko"):
    """币 id → 缓存 key。CoinPaprika 来的数值单独存 `paprika:<gecko id>`，别和 CoinGecko 的混。"""
    cid = _str(cid)
    cid = cid.strip() if cid else ""
    if not cid:
        return None
    return cid if origin == "coingecko" else "paprika:" + cid


def _market_load_locked(now):
    """（锁内）惰性读盘。不存在 / 写坏 / 字段不对 → 当空，下次写入自愈。"""
    if _MARKET_LOADED[0]:
        return
    _MARKET_LOADED[0] = True
    if not MARKET_CACHE_PERSIST:      # ⛔ 内存模式：不读盘
        return
    obj, ok = _load(MARKET_CACHE_JSON)
    if not ok:
        return
    for key, rec in _dict(_dict(obj).get("entries")).items():
        rec = _dict(rec)
        ts, base = _num(rec.get("ts")), rec.get("base")
        if not _str(key) or not isinstance(base, dict) or ts is None:
            continue
        if now - float(ts) > MARKET_CACHE_KEEP:
            continue
        origin = "coinpaprika" if _str(rec.get("origin")) == "coinpaprika" else "coingecko"
        _MARKET_CACHE[key.strip()] = {"ts": float(ts), "base": base, "origin": origin}


def _market_save_locked():
    """（锁内）原子落盘（tmp + os.replace）。写失败就当没这回事，绝不影响查询。"""
    if not MARKET_CACHE_PERSIST:      # ⛔ 内存模式：不写盘（用户要求不在磁盘上落任何东西）
        return
    items = sorted(_MARKET_CACHE.items(), key=lambda kv: kv[1]["ts"],
                   reverse=True)[:MARKET_CACHE_MAX]
    tmp = MARKET_CACHE_JSON + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"ts": _now(), "entries": dict(items)}, fh, ensure_ascii=False)
        os.replace(tmp, MARKET_CACHE_JSON)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def _market_get(cid, now=None):
    """按币 id 查缓存 → (记录, 年龄秒) / (None, None)。新鲜、过期**都**返回，TTL 由调用方判。
    先看 CoinGecko 那份（更准），没有再看 CoinPaprika 那份。"""
    now = time.time() if now is None else now
    with _MARKET_LOCK:
        _market_load_locked(now)
        for origin in ("coingecko", "coinpaprika"):
            key = _market_key(cid, origin)
            rec = _MARKET_CACHE.get(key) if key else None
            if rec:
                return rec, max(0.0, now - rec["ts"])
    return None, None


def _market_put(bases, origin="coingecko"):
    """{币 id: 行情字段} **逐个按 id 拆开**存 → 落盘（一次 /coins/markets 的 N 个币 = N 条）。"""
    bases = _dict(bases)
    if not bases:
        return
    now = time.time()
    with _MARKET_LOCK:
        _market_load_locked(now)
        for cid, base in bases.items():
            key = _market_key(cid, origin)
            if not key or not isinstance(base, dict):
                continue
            _MARKET_CACHE[key] = {"ts": now, "base": dict(base), "origin": origin}
        _market_save_locked()


def _market_origin_source(rec):
    """缓存记录 → result.source：**只要行情是从缓存来的就标 cache**（前端显示「源:缓存(N 分钟前)」，
    绝不把 10 分钟前的数据冒充成刚拉的）；CoinPaprika 那份数值因为身份仍来自 CoinGecko → mixed
    （保留「降级」信号，前端标「源:CoinGecko+CoinPaprika(降级)」）。"""
    return "mixed" if _dict(rec).get("origin") == "coinpaprika" else "cache"


def _stale_note(kind, ages, code):
    """陈旧缓存的 missing 文案：写清「为什么用旧的」+「几分钟前」+ 哪些币。"""
    mins = max(1, int(round(max(ages.values()) / 60.0)))
    ids = "、".join(sorted(ages))
    if kind == "miss":
        return "CoinGecko 行情明细缺 %s，用的是 %d 分钟前的缓存" % (ids, mins)
    if code == 429:
        return "CoinGecko 限流(429)，用的是 %d 分钟前的缓存（%s）" % (mins, ids)
    return "CoinGecko 行情失败，用的是 %d 分钟前的缓存（%s）" % (mins, ids)


# ---- CoinPaprika 降级源（只在 CoinGecko 自己失败时才走；不进 CoinGecko 的限速槽） ----

def _paprika_search(q, deadline=None):
    """CoinPaprika 检索 → (currencies, err)。失败不抛异常。"""
    data, err, _code, _rl = _http_json(URL_PAPRIKA_SEARCH % urllib.parse.quote(q),
                                       throttled=False, deadline=deadline,
                                       source="coinpaprika")
    return _list(_dict(data).get("currencies")), err


def _paprika_ticker(pid, deadline=None):
    """CoinPaprika 单个币的即时行情 → (ticker, err)。失败返回 (None, err)。"""
    data, err, _code, _rl = _http_json(URL_PAPRIKA_TICKER % urllib.parse.quote(pid),
                                       throttled=False, deadline=deadline,
                                       source="coinpaprika")
    return (data if isinstance(data, dict) else None), err


def _paprika_usd(rec):
    """CoinPaprika 的 ticker 记录 → quotes.USD 字典（缺就空 dict）。"""
    return _dict(_dict(_dict(rec).get("quotes")).get("USD"))


# 全量快照的内存缓存（⛔ 用户要求一个文件都不落：_PAPRIKA_BULK 只活在进程里）
_PAPRIKA_LOCK = threading.Lock()
_PAPRIKA_BULK = {"ts": 0.0, "idx": {}, "by_id": {}, "err": None}
#   idx:    {SYMBOL(大写): 该 symbol 的 ticker 记录}；同名多条取 market_cap 最大的那条
#   by_id:  {CoinPaprika id: 同一条记录} —— 检索结果（id/symbol 都是 CoinPaprika 的）按 id 直接对号


def _paprika_bulk(deadline=None):
    """CoinPaprika 全量行情（GET /v1/tickers，一次 ~1.6MB / 2000 个币）→ {SYMBOL: 记录}。
    **按 symbol 对号**（不是按 id）：CoinGecko 的 `polkadot` 在 CoinPaprika 叫
    `dot-polkadot-token`，id 根本不通，所以行情降级只能认 symbol。
    同名多条取 market_cap 最大的一条（SOL → sol-solana，而不是 sol-binance-peg-sol）。
    快照在内存里缓存 PAPRIKA_BULK_TTL 秒；**只放内存、绝不落盘**；失败返回 {}，不抛异常。"""
    now = time.time()
    with _PAPRIKA_LOCK:
        if _PAPRIKA_BULK["idx"] and now - _PAPRIKA_BULK["ts"] < PAPRIKA_BULK_TTL:
            return _PAPRIKA_BULK["idx"]
    data, err, _code, _rl = _http_json(URL_PAPRIKA_BULK, throttled=False, deadline=deadline,
                                           source="coinpaprika")
    if not isinstance(data, list):
        with _PAPRIKA_LOCK:
            _PAPRIKA_BULK.update(ts=time.time(), idx={}, by_id={},
                                 err=err or "返回格式不是列表")
        return {}
    picked = {}                               # SYMBOL -> (market_cap, 记录)
    for rec in data:
        if not isinstance(rec, dict):
            continue
        sym = _sym(rec.get("symbol"))
        if not sym:
            continue
        mcap = _num(_paprika_usd(rec).get("market_cap"))
        cur = picked.get(sym)
        if cur is None or (mcap is not None and (cur[0] is None or mcap > cur[0])):
            picked[sym] = (mcap, rec)
    idx = {sym: rec for sym, (_mcap, rec) in picked.items()}
    by_id = {}
    for rec in data:
        rid = _str(_dict(rec).get("id"))
        if rid and rid.strip() and rid.strip() not in by_id:
            by_id[rid.strip()] = rec
    with _PAPRIKA_LOCK:
        _PAPRIKA_BULK.update(ts=time.time(), idx=idx, by_id=by_id, err=None)
    return idx


def _paprika_by_id():
    """同一次全量快照里的 {CoinPaprika id: 记录}（**零额外请求**；快照新鲜时才有）。"""
    now = time.time()
    with _PAPRIKA_LOCK:
        if not _PAPRIKA_BULK["idx"] or now - _PAPRIKA_BULK["ts"] >= PAPRIKA_BULK_TTL:
            return {}
        return dict(_PAPRIKA_BULK.get("by_id") or {})


def _paprika_rows_for(q, coins, missing, deadline=None):
    """CoinGecko 这批判 coins 的行情用 CoinPaprika 全量快照**按 symbol** 兜底
    → {gecko_id: result row}（拿不到的写 missing，不进返回）。
    身份字段（id/symbol/名称）仍用 CoinGecko 的，数值来自 CoinPaprika → source="mixed"；
    一个数都没填上 / symbol 对不上 → 不返回（调用方记 source="none"）；
    TVL / 类别 / 链 CoinPaprika 没有 → null（哪怕是 mixed 行也不拿 DefiLlama 硬凑）。"""
    idx = _paprika_bulk(deadline)
    by_id = _paprika_by_id()
    if not idx:
        with _PAPRIKA_LOCK:
            why = _PAPRIKA_BULK["err"] or "全量端点无数据"
        missing.append("CoinPaprika 批量行情不可用（%s），这些币的行情只能记缺失" % why)
        return {}
    out = {}
    for coin in coins:
        cid = (_str(coin.get("id")) or "").strip()
        if not cid:
            continue
        sym = _sym(coin.get("symbol"))
        # 按 id 对号只在 symbol 也对得上时才认（id 撞车也绝不张冠李戴）
        rec = by_id.get(cid)
        if rec is not None and sym and _sym(rec.get("symbol")) and _sym(rec.get("symbol")) != sym:
            rec = None
        if rec is None:
            rec = idx.get(sym) if sym else None
        if rec is None:
            missing.append("CoinPaprika 全量里没有 %s（symbol=%s）" % (cid, sym or "无"))
            continue
        base = _base_from_bulk(coin, rec)
        if not _has_market_values(base):       # 一个数都没填上 → 别硬造一行 source=mixed
            missing.append("CoinPaprika 全量里 %s 没有任何行情数字" % cid)
            continue
        # 存进缓存时按 `paprika:<gecko id>` 记（数值来源和 CoinGecko 那份分开存，不互相污染）
        _market_put({cid: base}, origin="coinpaprika")
        out[cid] = _result_row(base, None, source="mixed")
    return out


def _paprika_only_rows(q, missing, deadline=None):
    """CoinGecko /search 就失败的兜底：直接用 CoinPaprika 检索 + 逐个 ticker 出结果
    → source="coinpaprika"（id 是 CoinPaprika 的 id，如 btc-bitcoin）。
    ticker 也没拿到（一个数都没填上）→ source="none"，别让前端以为有降级数据。
    ⚠️ 精度较低：距ATH 是两位小数粗估、7d/30d/1y 常缺（写 null）、TVL/类别/链没有（null）。"""
    currencies, err = _paprika_search(q, deadline)
    picked = order_search_coins(currencies, q)
    if not picked:
        missing.append(("CoinPaprika 检索也失败（%s）" % err) if err else "CoinPaprika 检索无匹配")
        return []
    # 行情：先吃**批量快照**（一次 ~1.6MB 覆盖 ~2000 个币、内存缓存 10 分钟、零额外请求），
    # 按 id 对号（检索结果本来就是 CoinPaprika 的 id）、再按 symbol 兜一层；
    # 快照里没有 / 快照拿不到 → 才老实回落到逐个 /tickers/<id>。补不到就留 null + 原因。
    idx = _paprika_bulk(deadline)
    by_id = _paprika_by_id()
    out = []
    for c in picked:
        pid = _str(c.get("id"))
        pid = pid.strip() if pid else ""
        if not pid:
            continue
        sym = _sym(c.get("symbol"))
        rec = by_id.get(pid)
        if rec is not None and sym and _sym(rec.get("symbol")) and _sym(rec.get("symbol")) != sym:
            rec = None
        if rec is None:
            rec = idx.get(sym) if sym else None
        if rec is None:
            ticker, terr = _paprika_ticker(pid, deadline)
            if ticker is None:
                missing.append("CoinPaprika 批量快照里没有 %s（symbol=%s），逐个 ticker 也没拿到"
                               "（%s）：这些字段记 null，绝不猜"
                               % (pid, sym or "无", terr or "无数据"))
            rec = ticker
        base = _base_from_paprika(c, rec)
        out.append(_result_row(base, None,
                               source="coinpaprika" if _has_market_values(base) else "none"))
    return out


def _gt_name_rows(q, missing, deadline=None):
    """名称搜索的**补充源**（NAME_SOURCE_ORDER 第 3 档）：GeckoTerminal /search/pools 按名字搜池子。
    有的币 CoinPaprika 有、有的**只有池子**（新 meme 币常常还没进 CoinGecko / CoinPaprika）。
    每行标 kind="pool_hit" + 链名（链名就藏在 data[].id 里 → 一次调用定链，⛔ 也不落盘）。
    0 条 / 报错都把「哪个源、什么错」写进 missing，再交回调用方走本地索引。"""
    rows, note = _gt_search_rows(q, deadline, is_address=False)
    if note:
        missing.append(note)
    return rows


def _local_index_rows(q, tvl_idx, missing):
    """名称搜索的**最后一个源**：本地符号索引（/coins/list 的内存副本）+ 按币 id 的内存行情缓存。
    只吃内存、**一个上游请求都不发**；索引里没有 / 缓存里没行情的币 → 写清原因，
    一个行情数都没有的行标 source="none"（标 coingecko 就是谎报来源）。"""
    picked = order_search_coins(_local_index_match(q) or [], q)
    if not picked:
        missing.append("本地符号索引里也没有「%s」（它只能认已经缓存过的币，不发现场请求）" % q)
        return []
    out, now = [], time.time()
    for coin in picked:
        cid = (_str(coin.get("id")) or "").strip()
        if not cid:
            continue
        rec, age = _market_get(cid, now)
        if rec is None:
            missing.append("本地索引命中 %s，但按币 id 的行情缓存里没有它（不发请求，行情记 null）"
                           % cid)
            out.append(_result_row(_base_from_search(coin), tvl_idx.get(cid), source="none"))
            continue
        base = dict(_dict(rec).get("base"))
        if base.get("market_cap_rank") is None:
            base["market_cap_rank"] = _int(coin.get("market_cap_rank"))
        out.append(_result_row(base, tvl_idx.get(cid),
                               source=_market_origin_source(rec), age_s=age))
    return _sort_rows_by_rank(out)


# ---- ② DefiLlama 直连行情（最后一道兜底：只补现价）----

def _llama_prices(ids, deadline=None):
    """coins.llama.fi 直连批量取现价（⛔ **不走代理**：代理整段断掉时它是唯一还活着的行情源）
    → {gecko_id: {"price": …, "symbol": …}}。一批 ≤ LLAMA_PRICE_BATCH 个，失败返回 {}、绝不抛异常。
    某个币没有价格（confidence 低 / 未收录）就当没拿到，不硬编。"""
    wanted = [(_str(i) or "").strip() for i in _list(ids)]
    wanted = [i for i in wanted if i]
    out = {}
    for start in range(0, len(wanted), LLAMA_PRICE_BATCH):
        batch = wanted[start:start + LLAMA_PRICE_BATCH]
        # 冒号**不要**转义：coins.llama.fi 认的就是字面量 `coingecko:<id>`（真机验证过）
        url = URL_LLAMA_PRICES % ",".join(
            "coingecko:" + urllib.parse.quote(cid, safe="") for cid in batch)
        data, _err, _code, _rl = _http_json(url, throttled=False, deadline=deadline,
                                            opener=_DIRECT_OPENER, breaker=False)
        coins = _dict(_dict(data).get("coins"))
        for cid in batch:
            rec = _dict(coins.get("coingecko:" + cid))
            price = _num(rec.get("price"))
            if price is not None:
                out[cid] = {"price": price, "symbol": _sym(rec.get("symbol"))}
    return out


def _llama_price_rows(coins, missing):
    """④ 后面的最后一道：DefiLlama 直连只补 price_usd（source="defillama"）
    → {gecko_id: result row}。身份字段用 CoinGecko 的（候选池/观察池靠 gecko id 对得上），
    市值 / 24h量 / 距ATH 仍然是 null —— 这个源没有这些字段，绝不拿别的数糊上去。
    ⚠️ 故意**不接**查询的 deadline：代理把预算烧光时，这一发直连反而更该发出去。
    ⛔ 也故意**不写进 _MARKET_CACHE**：那是「按币 id 的完整行情」，只带现价的一条进去会把
       市值 / 24h量 / 距ATH 全盖成 null（下次查询就再也拿不到真数据了）。"""
    ids = []
    for coin in coins:
        cid = (_str(_dict(coin).get("id")) or "").strip()
        if cid:
            ids.append(cid)
    if not ids:
        return {}
    # 自己的小预算：代理把 12 秒烧光时，这一发直连仍然要发得出去（见 LLAMA_PRICE_BUDGET）
    own = time.monotonic() + LLAMA_PRICE_BUDGET
    got = _llama_prices(ids, own)
    lack = [cid for cid in ids if cid not in got]
    if lack:      # 到了这一步就说明这些币真的没有别的来源了 → 写清最后一个源也没兜住
        missing.append("DefiLlama 直连也没拿到 %s 的现价（不走代理的最后一个源）"
                       % "、".join(lack))
    if not got:
        return {}
    out = {}
    for coin in coins:
        cid = (_str(_dict(coin).get("id")) or "").strip()
        rec = got.get(cid)
        if not cid or rec is None:
            continue
        base = _base_from_search(coin)
        base["price_usd"] = rec["price"]
        if not _str(base.get("symbol")) and rec.get("symbol"):
            base["symbol"] = rec["symbol"]
        out[cid] = _result_row(base, None, source="defillama")
    return out


# 桥接 / 包装版（同名同 rank 时往后排）：near 会同时命中 `near` 和
# `binance-peg-near-protocol`（都是 rank 38），不加这条正牌会被桥接版挤到后面。
_BRIDGE_MARKS = ("binance-peg-", "rainbow-bridged-", "bridged-", "wrapped-",
                 "-peg-", "wormhole-", "portal-", "multichain-")


def _sort_rows_by_rank(rows):
    """本地索引命中的结果按 market_cap_rank 重排（None 垫底）。
    索引里没有排名（/coins/list 只有 id/symbol/name），`eth` 这种会命中一堆同名小币，
    位次靠前的先出现 —— 和原来 /search 的口径（rank 升序）保持一致。
    ⚠️ rank 并列时必须再定序（sort 稳定 = 靠输入顺序，会冒出 binance-peg-* 这种）：
    桥接/包装版往后 → id 短的靠前（`near` 比 `rainbow-bridged-near-ether` 正牌）→ 字典序。"""
    def key(r):
        rank = r.get("market_cap_rank")
        cid = _str(r.get("id")) or ""
        bridged = any(m in cid for m in _BRIDGE_MARKS)
        return (rank is None, rank or 0, bridged, len(cid), cid)
    return sorted(rows, key=key)


# ---- 两条查询流程 ----

def _do_search(q, tvl_idx, missing, deadline=None):
    """查词的解析顺序（NAME_SOURCE_ORDER = CoinGecko → CoinPaprika → GeckoTerminal 池子搜索
    → 本地符号索引）：
    ① 本地符号索引（/coins/list 的**内存副本**，命中就省掉一次 /search —— 它就是 CoinGecko 那一档的加速器）
    ② CoinGecko /search（索引没命中才发）
    ③ **只有 CoinGecko 自己失败**才降级 CoinPaprika /search（中文可搜、按 rank 升序、批量 ticker 补行情）
    ④ 上面都空手 → GeckoTerminal /search/pools 按名字搜**池子**（补充源：有的币只有池子；
       行标 kind="pool_hit" + 链名，一次调用定链）
    ⑤ 还空手 → 才用本地符号索引当最后一个源（只吃内存；索引若是查询中途才拉回来，
       这一道就能救场）
    检索成功但没这个词 → 真的没有，不用降级。
    行情五道：新鲜内存缓存 → CoinGecko → 过期内存缓存 → CoinPaprika 全量快照（按 id/symbol）
    → DefiLlama 直连（只补现价）→ 记缺失。"""
    picked = order_search_coins(_local_index_match(q) or [], q)
    from_index = bool(picked)                 # 索引命中：检索那一次请求省了
    if not picked:
        data, err, _code, _rl = _http_json(URL_GECKO_SEARCH % urllib.parse.quote(q),
                                           deadline=deadline)
        picked = order_search_coins(_list(_dict(data).get("coins")), q)
        if not picked:
            if err:
                missing.append("CoinGecko 检索失败（%s），已降级 CoinPaprika（精度较低）" % err)
                rows = _paprika_only_rows(q, missing, deadline)
                if rows:
                    _publish_struct_row(rows[0])       # 结构取数立刻开跑（和行情富化并行）
                    return rows
            # 补充源：CoinGecko / CoinPaprika 都没有的币，问 GeckoTerminal 的**池子**搜索
            #（/search/pools 按名字，一次调用定链；每行 kind="pool_hit" + 链名）
            rows = _gt_name_rows(q, missing, deadline)
            if rows:
                _publish_struct_row(rows[0])
                return rows
            if err:
                # 最后一个源（NAME_SOURCE_ORDER 尾巴）：本地符号索引 —— 只吃内存，不再发请求
                rows = _local_index_rows(q, tvl_idx, missing)
                if rows:
                    _publish_struct_row(rows[0])
                return rows
            return []
    # 第 1 条的身份（id + symbol）**已经确定** → 立刻交给并行的结构取数任务：
    # 下面那几道行情富化（/coins/markets → 陈旧缓存 → CoinPaprika → DefiLlama）再慢也饿不死它。
    _publish_struct_row({"id": _str(_dict(picked[0]).get("id")),
                         "symbol": _sym(_dict(picked[0]).get("symbol")),
                         "name": _str(_dict(picked[0]).get("name"))})
    ids = [c["id"].strip() for c in picked]

    # ① 先吃按币 id 的缓存：新鲜命中直接用；过期条目留着兜底（但仍然优先试网络）
    bases, stale, need = {}, {}, []      # cid -> (base, source, age_s)
    now = time.time()
    for cid in ids:
        rec, age = _market_get(cid, now)
        if rec is None:
            need.append(cid)
        elif age < MARKET_TTL:
            bases[cid] = (dict(_dict(rec).get("base")), _market_origin_source(rec), age)
        else:
            stale[cid] = (rec, age)
            need.append(cid)

    # ② 只对没命中的 id 发 /coins/markets（全命中 → 一次请求都不发）
    derr, dcode = None, None
    if need:
        detail, derr, dcode, _drl = _http_json(
            URL_GECKO_MARKETS % urllib.parse.quote(",".join(need), safe=","), deadline=deadline)
        by_id = {}
        for r in _list(detail):
            if isinstance(r, dict):
                cid = _str(r.get("id"))
                if cid:
                    by_id[cid.strip()] = r
        for cid in need:
            if cid in by_id:
                bases[cid] = (_base_from_market(by_id[cid]), "coingecko", None)
        # 一次请求拿到 N 个币 → 逐个按 id 拆开存进 _MARKET_CACHE（下一次换词查同一个币零请求）
        _market_put({cid: bases[cid][0] for cid in need if cid in bases})

    # ③ 失败（429 / 连接类）或明细里真没有 → 陈旧兜底：有总比「—」强，且写明是几分钟前的
    ages_stale = {}
    for cid in need:
        if cid in bases or cid not in stale:
            continue
        rec, age = stale[cid]
        bases[cid] = (dict(_dict(rec).get("base")), "cache", age)
        ages_stale[cid] = age
    if ages_stale:
        missing.append(_stale_note("miss" if not derr else "fail", ages_stale, dcode))

    # ④ 还没有的 → CoinPaprika 降级（独立额度，不受 CoinGecko 429 影响）
    #    ⚠️ 只能按 symbol 在全量快照里对号：CoinGecko 的 id（polkadot）和 CoinPaprika 的 id
    #    （dot-polkadot-token）不通，拿 id 逐个查必然空手（2026-09-15 真机事故）。
    miss = [c for c in picked if (c.get("id") or "").strip() not in bases]
    if miss:
        if derr:
            missing.append("CoinGecko 行情明细失败（%s），已降级 CoinPaprika"
                           "（精度较低；距ATH 为两位小数粗估）" % derr)
        missing.append("CoinGecko 行情明细缺 %s" % "、".join(
            (c.get("id") or "").strip() for c in miss))
    pap = _paprika_rows_for(q, miss, missing, deadline) if miss else {}

    # ⑤ 最后一道：DefiLlama **直连**（⛔ 不走代理 / 不受代理熔断闸门限制）
    #    —— 代理整段断掉时唯一还活着的源，只补 price_usd。
    still = [c for c in picked
             if (c.get("id") or "").strip() not in bases
             and (c.get("id") or "").strip() not in pap]
    llama = _llama_price_rows(still, missing) if still else {}

    out = []
    for c in picked:
        cid = (c.get("id") or "").strip()
        if cid in pap:                       # 已由 CoinPaprika 兜底（source=mixed）
            out.append(pap[cid])
            continue
        if cid in llama:                     # 已由 DefiLlama 直连兜底（source=defillama）
            out.append(llama[cid])
            continue
        base, src, age_s = bases.get(cid, (None, None, None))
        if base is None:                     # 都没有兜住 → 只留 /search 的字段
            base = _base_from_search(c)
            # 一个行情数都没填上 → 必须是 none（标 coingecko 等于向前端谎报数据来源）
            src = "coingecko" if _has_market_values(base) else "none"
            age_s = None
        elif base.get("market_cap_rank") is None:
            base["market_cap_rank"] = _int(c.get("market_cap_rank"))
        out.append(_result_row(base, tvl_idx.get(cid), source=src, age_s=age_s))
    if from_index:
        # 本地索引没有排名 → 拿 /coins/markets 刚给的 rank 重排，等价于 /search 的口径
        out = _sort_rows_by_rank(out)
    return out


# ---------------- 地址查询（源顺序：GMGN search → DexScreener → GeckoTerminal → CoinGecko）----
# 2026-09-15 起 **GMGN market search -q <地址> 是第一顺位**（独立主机 + 独立熔断桶）：
# 真机那次代理半死，GMGN 完全健康（resolve 2.06s 成功）却因为别的源全挂而查出 0 条。
#
# 2026-09-15 真机硬证据（用户 Clash 部分路由挂掉时）：
#   直连:    DNS 全超时 ✗（只有 coins.llama.fi + 百度通）
#   经代理:  GeckoTerminal ✓1.5s / CoinPaprika ✓0.6s / Telegram ✓7.4s / 百度 ✓
#            CoinGecko ✗URLError / DexScreener ✗0.6s 秒断 / Binance ✗
#   → 用户看到的「查询失败」= 所有源都压在 CoinGecko/DexScreener 上，而这两个正好被路由到死节点。
#   所以地址查询的主源换成 GeckoTerminal（按地址查池子，全链可用），老路径一律**保留**当备用。

def _gt_nets_for(addr):
    """按输入形态决定先试哪些 GeckoTerminal network：base58 → ("solana",)；
    0x → 逐链 eth/base/bsc/arbitrum/polygon/optimism/gnosis（顺序照任务给的名单）。"""
    a = (_str(addr) or "").strip()
    if _MODE_EVM_RE.match(a):
        return GT_EVM_NETS
    if _MODE_SOL_RE.match(a):
        return GT_SOL_NETS
    return GT_EVM_NETS


def _gt_source(net):
    """每行的 source 说实话：写清是哪个 net 的哪个端点。"""
    return "geckoterminal /networks/%s/tokens/…/pools" % net


def _gt_chain_name(net):
    """GeckoTerminal network id → 人话；不认识的**原样返回**，绝不硬编（同 _ds_chain_name 的口径）。"""
    n = (_str(net) or "").strip().lower()
    if not n:
        return None
    return GT_NET_LABELS.get(n, n)


def _gecko_platform_for(chain_id):
    """chain_id（DexScreener 的 chainId 或 GeckoTerminal 的 network id）→ CoinGecko platform。
    对不上就 None（调用方写清原因，绝不猜）。"""
    cid = (_str(chain_id) or "").strip().lower()
    if not cid:
        return None
    return GT_NET_GECKO_PLATFORM.get(cid) or DS_CHAIN_GECKO_PLATFORM.get(cid)


# quote 侧是这些东西时，池子名（"PEPE / USDC 1%"）里只有 base 侧有意义 → name 只留 base
_GT_QUOTE_NOISE = frozenset((
    "USDC", "USDT", "DAI", "BUSD", "TUSD", "USDD", "USDE", "USDS", "FDUSD", "PYUSD",
    "GUSD", "LUSD", "SUSD", "USD1", "USDX", "USDC.E", "USDT.E",
    "WETH", "WBNB", "WSOL", "WMATIC", "WPOL", "WAVAX", "WFTM", "WCELO", "WGLMR",
))
_GT_FEE_RE = re.compile(r"\s+\d+(?:\.\d+)?%$")     # "USDC 1%" / "WBNB 0.01%" 的费率后缀


def _gt_split_pool_name(raw):
    """GeckoTerminal 的 attributes.name（"PEPE / USDC 1%" = base / quote）→ (symbol, name)。
    · symbol = "/" 左侧的 base 侧
    · name：quote 侧（去掉 1% 这种费率后缀后）是稳定币/WETH → **只取 base 侧**；
      否则保留 "BASE / QUOTE"（池子名，带费率档，信息量更大）
    拆不出来 → (能拿到的部分, 原名)，绝不硬编。"""
    text = _str(raw)
    text = text.strip() if text else ""
    if not text:
        return None, None
    left, sep, right = text.partition("/")
    base = left.strip() or None
    if not sep:
        return base, text
    quote = _GT_FEE_RE.sub("", right.strip()).strip()
    if not base:
        return None, text
    if quote.upper() in _GT_QUOTE_NOISE or not quote:
        return base, base
    return base, "%s / %s" % (base, quote)


def _gt_dex_id(pool):
    """relationships.dex.data.id（如 pancakeswap_v2）—— 同一次响应里就有的真字段，零额外请求。"""
    rel = _dict(_dict(pool).get("relationships"))
    return _str(_dict(_dict(rel.get("dex")).get("data")).get("id"))


def _gt_row_from_pool(pool, addr, net):
    """GeckoTerminal /tokens/{addr}/pools 的一条 → 结果行。
    字段**只用真机核过的那些**，缺的一律 null + 一句原因（绝不猜、绝不拿别的数顶替）。"""
    attrs = _dict(_dict(pool).get("attributes"))
    if not attrs:
        return None
    symbol, name = _gt_split_pool_name(attrs.get("name"))
    price_usd = _num(attrs.get("base_token_price_usd"))
    liq = _num(attrs.get("reserve_in_usd"))
    fdv = _num(attrs.get("fdv_usd"))
    mcap = _num(attrs.get("market_cap_usd"))
    v24 = _num(_dict(attrs.get("volume_usd")).get("h24"))
    ch24 = _num(_dict(attrs.get("price_change_percentage")).get("h24"))
    created = _str(attrs.get("pool_created_at"))
    created = created.strip() if created else None
    pool_addr = _str(attrs.get("address"))
    pool_addr = pool_addr.strip() if pool_addr else None
    nulls = {}
    if price_usd is None:
        nulls["price_usd"] = "该池没给 base_token_price_usd"
    if mcap is None:
        nulls["mcap_usd"] = "该池没给 market_cap_usd（不拿 fdv 顶替市值）"
    if fdv is None:
        nulls["fdv"] = "该池没给 fdv_usd"
    if liq is None:
        nulls["liquidity_usd"] = "该池没给 reserve_in_usd"
    if v24 is None:
        nulls["volume_24h"] = "该池没给 volume_usd.h24"
    if ch24 is None:
        nulls["price_change_24h"] = "该池没给 price_change_percentage.h24"
    if created is None:
        nulls["pool_created_at"] = "该池没给 pool_created_at"
    if pool_addr is None:
        nulls["pair_address"] = "该池没给 attributes.address"
    nulls["market_cap_rank"] = "GeckoTerminal 不提供市值排名"
    nulls["drawdown_from_ath_pct"] = "GeckoTerminal 不提供距ATH（CoinGecko 能补就补）"
    nulls["ret_7d_pct"] = nulls["ret_30d_pct"] = nulls["ret_1y_pct"] = \
        "GeckoTerminal 池子接口只给 24h 涨跌，不给 7d/30d/1y"
    if not symbol:
        nulls["symbol"] = "attributes.name 里没有 \"/\" 左侧的 base 符号"
    if symbol and _is_pair_like_name(symbol, name):
        # 改动④：这一行来自**交易对 / 池子**那条路（attributes.name 的 base 侧只是池名的一半）
        # → 池名一个字符都不许参与 symbol（含左半截：`BONK / SOL` → `BONK` 也不行）：
        #   symbol 记 null + 原因；name 是显示标签，**保留池名**（面板要看出用户查的是哪个池子）。
        nulls["symbol"] = ("GeckoTerminal 池名 / 对子名「%s」不是代币符号 → 记 null，"
                           "不拿池名顶替" % (name or symbol))
        symbol = None
    chain_name = _gt_chain_name(net)
    base = {
        "id": None,          # GeckoTerminal 没有 CoinGecko id；补不到就 null，绝不拿地址冒充
        "symbol": _sym(symbol),
        "name": name,
        "market_cap_rank": None,
        "price_usd": price_usd,
        "drawdown_from_ath_pct": None,
        "ret_7d_pct": None, "ret_30d_pct": None, "ret_1y_pct": None,
        "mcap_usd": mcap,
        "vol24h_usd": v24,
        "chain_id": net,
        "chain_name": chain_name,
        "chain": net,          # 逐链路径的链名（net）也带上：下游按「链 + 合约地址」取日线要用
        "dex_id": _gt_dex_id(pool),
        "address": (_str(addr) or "").strip() or None,
        "pair_address": pool_addr,
        "liquidity_usd": liq,
        "fdv": fdv,
        "volume_24h": v24,
        "price_change_24h": ch24,
        "pair_created_at": None,        # 老字段：GeckoTerminal 给的是 ISO 的 pool_created_at
        "pool_created_at": created,
        "null_fields": nulls,
    }
    row = _result_row(base, None, source=_gt_source(net))
    # 链名也塞进 chains：前端那张卡「链」那一行就能显示「BSC 币安智能链」
    row["chains"] = [chain_name] if chain_name else None
    return row


# ---- GeckoTerminal /search/pools：**一次调用定链**（地址主源 + 名称搜索的补充源）----
# 端点/字段见 URL_GT_SEARCH_POOLS 上面那段真机记录。这里只做三件事：
#   ① 从 data[].id 取 network（链名在这里，未知 network 原样显示，绝不硬编）
#   ② 判断查的是**代币**还是**池子**（base_token 地址 == 查询地址 → 代币）
#   ③ 每个池一行、按流动性降序、最多 GT_SEARCH_LIMIT 行；缺字段 null + 原因
def _row_liq_key(row):
    """排序键：流动性（reserve_in_usd → liquidity_usd）降序；没有流动性的垫底。"""
    liq = _num(_dict(row).get("liquidity_usd"))
    return liq if liq is not None else -1.0


def _iso_date_only(v):
    """ISO 8601（"2023-04-17T18:43:35Z"）→ 日期（"2023-04-17"）；认不出就 None（绝不瞎截）。"""
    s = (_str(v) or "").strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[T ]|$)", s)
    return m.group(1) if m else None


def _gt_search_net(pool):
    """data[].id = "<network>_<address>" → network（**链就藏在这里**，如 "bsc_0x0ed7e529…"）。
    拆不出来 → None（调用方写清原因，绝不硬编一条链）。"""
    pid = (_str(_dict(pool).get("id")) or "").strip()
    if not pid:
        return None
    net, sep, _addr = pid.partition("_")
    net = net.strip().lower()
    if not sep or not net:
        return None
    return net


def _gt_search_chain_name(net):
    """GT /search/pools 的 network → 人话：先查 GT 自己那张真机表（GT_NET_LABELS），
    再查 DexScreener 风格的 DS_CHAIN_NAMES；都认不出就**原样显示**，绝不硬编一个名字上去。"""
    n = (_str(net) or "").strip().lower()
    if not n:
        return None
    return GT_NET_LABELS.get(n) or DS_CHAIN_NAMES.get(n) or n


def _gt_rel_token_addr(pool, side):
    """relationships.<side>.data.id（"<net>_<代币地址>"）→ 代币地址（去掉 network 前缀）。
    读不出来 → None（调用方不猜）。"""
    rel = _dict(_dict(pool).get("relationships"))
    ident = (_str(_dict(_dict(rel.get(side)).get("data")).get("id")) or "").strip()
    if not ident:
        return None
    _net, sep, addr = ident.partition("_")
    return ((addr.strip() if sep else ident) or None)


def _gt_pool_own_addr(pool):
    """池子**自己**的地址：data[].id 的 "<network>_<池地址>" 后缀（真机字段），
    读不出来再退 attributes.address；都没有 → None（调用方不猜）。"""
    pid = (_str(_dict(pool).get("id")) or "").strip()
    _net, sep, addr = pid.partition("_")
    if sep and addr.strip():
        return addr.strip()
    attrs = _dict(_dict(pool).get("attributes"))
    return (_str(attrs.get("address")) or "").strip() or None


# ---- 改动①（2026-09-16 真机）：池子 → 代币**只认 relationships + 自证**，LP 代币绝不当最终代币 ----
# 真机事故：同一个池子地址连着查两次，第二次 symbol=CAKE-LP（LP 代币）——「哪个源先答话」说了算。
# 本文件口径（**确定性**，不看源顺序、不看列表顺序）：
#   · 判定「用户给的是池子」→ 取代币**只允许**走池子 relationships 的 base_token / quote_token
#     （GT /search/pools 与 /networks/{net}/pools/{pool} 是同一份 relationships）
#   · 候选代币**必须自证**：GET /networks/{net}/tokens/{代币}/pools?page=1 **非空**才算「查得到池子」
#     ⚠️ 改动④（2026-09-16 真机事故）：老口径把「空 / 报错 / **限流**」揉成一句「没自证通过」→
#        GT 429（HTTP 429）被当成「这个代币没有池子」，于是两侧都判「查不到池子」、token 定了 None。
#        现在必须分开：**只有拿到了确定回答**（nodata=200 空 / notfound=404）才算「真没有池子」；
#        **rate / down 是不确定** → 不许当作「没有池子」，按下面的降级策略处理（保命优先但诚实标注）。
#        自证结论按 (链, 池子) 在内存里缓存（改动⑤）：确定回答 10 分钟、rate/down 只 60 秒。
#   · LP 代币黑名单：symbol / name 命中 → **不作为最终代币**（另一侧可用就用另一侧）
#   · 候选顺序**固定** base → quote；两侧都不可用 → 如实报「两侧代币在 GT 都查不到池子」+ 两侧地址与
#     symbol，**绝不猜**（也绝不退回池名 / 池子地址）
#   · **池名不参与**：`"BNC4 / 4Stock"` / `"CAKE-LP / BNC4"` 只是池子的文字标签，不等于代币符号
_LP_SYMBOL_EXACT = frozenset(("slp", "cake-lp", "uni-v2"))
_LP_SYMBOL_RE = re.compile(r"(?:^|[\s\-_/])lp$|v[23]$", re.IGNORECASE)


def _is_lp_token_name(symbol, name):
    """LP 代币黑名单：`*-LP`、`CAKE-LP`、`UNI-V2`、`SLP`、`*V2`、`*V3`（**大小写不敏感**）。

    symbol 或 name 命中即判为 LP（"Cake-LP" / "UNI-V2" / "SLP" / "BNB-LP" / "CAKEV2"…）。
    只按「整个名字的结尾（或精确等于）」判：`-LP` / `LP` / `V2` / `V3` —— "SOLP" 这种中间带 lp 的
    不误判（前面没有分隔符）。"""
    for raw in (symbol, name):
        s = (_str(raw) or "").strip().lower()
        if not s:
            continue
        if s in _LP_SYMBOL_EXACT or _LP_SYMBOL_RE.search(s):
            return True
    return False

# ---- 改动（2026-09-16 真机）：池名 / **对子名**不是 symbol ----
# 真机事故：GT 限流 → _gt_pool_precheck 判不出「这是池子」→ 降级「按代币处理」；
# 此时命中的是 DexScreener / GMGN 的「**交易对**」行（它们眼里池子地址就是个 pair / LP），
# 该行的 symbol 是**对子 base**（`QQQB`）、name 是**对子名**（`QQQB / 牛来`）→ 面板把左半截当 symbol。
# `_is_lp_token_name` 只认 `*-LP` / `CAKE-LP` / `UNI-V2` / `SLP` / `*V2` / `*V3` 这类后缀，
# **认不出「A / B」这种交易对名** → 这里补一层「对子名 / 池名」识别（与 LP 黑名单**并列**）。
# ⛔ 池名一个字符都不许参与 symbol。命中 → 该行**不许**把 symbol/name 当接口（记 why、继续看其它源）。
_PAIR_SEP_RE = re.compile(r"\s*/\s*|\s+[-\u2010-\u2015]\s+")


def _is_pair_like_name(sym, name=None):
    """「对子名 / 池名」识别（**大小写不敏感**）：命中任一即判为池名，不许当 symbol。

      1. `name` 或 `symbol` 里出现**对子分隔符**：`"/"`、`" / "`、`"／"`、`" - "`
         （前后带空格的中划线）—— 只拦「带空格的对子形态」，合法的 `USD-T` 这类照样放行；
      2. `name` 形如 `X / Y` 且 `symbol` **等于** X 或 Y 的那一半
         （正是 `QQQB / 牛来` → `QQQB` 的情形）。
    与 `_is_lp_token_name` 并列使用（两者命中任一都不许当 symbol）。"""
    s = (_str(sym) or "").strip()
    n = (_str(name) or "").strip()
    for raw in (n, s):
        if raw and _PAIR_SEP_RE.search(raw):
            return True
    if s and n:
        parts = [p.strip() for p in re.split(r"\s*/\s*", n) if p.strip()]
        if len(parts) >= 2:
            sl = s.lower()
            if any(p.lower() == sl for p in parts):
                return True
    return False


def _gt_pool_verdict_pool(pools, query, is_sol):
    """用户给的地址**就是某个池子自己**吗？→ 那个池子对象；不是池子 / 读不出来 → None。

    判定只看「池子自己的地址（data[].id 后缀）== 查询地址」，且查询地址**不是**它的 base/quote
    （那种是**代币**查询）。多条命中时按 (**流动性与否, 流动性降序, 池地址**) 取第一条 —— 固定口径，
    不依赖 GT 返回顺序（确定性；同一地址的池子只会有一条）。"""
    cands = []
    for pool in _list(pools):
        if not isinstance(pool, dict):
            continue
        own = _gt_pool_own_addr(pool)
        if not own or not _addr_same(own, query, is_sol):
            continue
        if (_addr_same(_gt_rel_token_addr(pool, "base_token"), query, is_sol)
                or _addr_same(_gt_rel_token_addr(pool, "quote_token"), query, is_sol)):
            continue            # 查询地址本人就是这条池子的某一侧 → 那是**代币**查询，不是池子
        liq = _num(_dict(_dict(pool).get("attributes")).get("reserve_in_usd"))
        sort_key = (0 if liq is not None else 1, -(liq if liq is not None else 0.0), own.lower())
        cands.append((sort_key, pool))
    if not cands:
        return None
    cands.sort(key=lambda t: t[0])
    return cands[0][1]


def _gt_pool_side_addrs(pool):
    """池子 relationships 的两侧 → [{side,label,address}, …]，顺序**固定** base → quote。"""
    out = []
    for side, label in (("base_token", "基础代币"), ("quote_token", "报价代币")):
        addr = _gt_rel_token_addr(pool, side)
        if addr:
            out.append({"side": side.replace("_token", ""), "label": label, "address": addr})
    return out


def _gt_pool_by_address(net, address, deadline=None):
    """改动①那一枪：GET /networks/{net}/pools/{pool} → 池子对象（含 relationships）；读不到 → None。

    与 /search/pools 命中的同一条池子是**同一份** relationships（真机核过）→ 平时不打（省额度），
    只有 /search/pools 那条**读不出** base/quote 时才补这一发（有 404 / 限流 / 熔断就如实放弃）。
    ⚠️ 沙箱无网：真实连通性**需 host 真机验证**。"""
    n = (_str(net) or "").strip()
    a = (_str(address) or "").strip()
    if not n or not a:
        return None, "没有「链 + 池地址」→ 拿不到池子的 relationships（不猜）"
    if _cb_is_open("geckoterminal"):
        return None, "GeckoTerminal 熔断中，这一发没发（池子的 relationships 拿不到）"
    if not _gt_call_ok():      # 改动③：本次查询的 GT 额度用完了 → 停手（用已有结果）
        return None, "GeckoTerminal %s（池子的 relationships 拿不到）" % _gt_capped_text()
    if not _gt_slot(deadline):
        return None, "GeckoTerminal 限流间隔 / 预算不够，这一发没发（拿不到 relationships）"
    url = URL_GT_POOL % (urllib.parse.quote(n), urllib.parse.quote(a))
    data, err, code, rl, _ra = _gt_json(url, deadline=deadline, budget=STRUCT_BUDGET_SEC)
    pool = _dict(_dict(data).get("data")) if isinstance(data, dict) else {}
    if pool and _gt_pool_side_addrs(pool):
        return pool, None
    if rl or code == 429:
        return None, "GeckoTerminal 限流(429)"
    if code == 404:
        return None, "GeckoTerminal 没这个池子（HTTP 404）"
    if code is None:
        return None, "连不上（%s）" % (err or "未知错误")
    return None, (err or "这条池子没给 relationships.base_token / quote_token")


# ---- 改动⑤：自证结论的内存缓存（⛔ 绝不落盘） ----
# 真机事故（2026-09-16）：同一个池子地址被连着查 / 被同一请求里的几条腿各查一遍 → 同一发自证
# 反复打，GT 那点额度（~30/min）全烧在重复自证上，再查就全是 429。现在按 (链, 池子) 记住结论：
#   · 拿到**确定回答**（自证通过 / 404 / 200 空）→ 缓存 10 分钟
#   · 结论建在 **rate / down** 上（降级用了 base_token）→ 只缓存 60 秒（额度一恢复就重问）
_GT_SELF_LOCK = threading.Lock()
_GT_SELF_CACHE = {}     # (net, 归一小写的池地址) -> (过期时刻, 结论 dict)


def _gt_self_key(net, own):
    """自证缓存的键：(链, 池子)。链或池地址缺一个 → None（拿不准的东西一律不缓存）。"""
    n = (_str(net) or "").strip().lower()
    a = (_gt_addr_key(own) or "").strip().lower()
    if not n or not a:
        return None
    return (n, a)


def _gt_self_get(key, now=None):
    """缓存命中 → 一份**深拷贝**的结论（调用方改它绝不能污染缓存）；没命中 / 过期 → None。"""
    if not key:
        return None
    now = time.monotonic() if now is None else now
    with _GT_SELF_LOCK:
        rec = _GT_SELF_CACHE.get(key)
        if rec is None:
            return None
        if rec[0] <= now:
            _GT_SELF_CACHE.pop(key, None)
            return None
        out = copy.deepcopy(rec[1])
    # 改动①：结论建在 rate / down 上（= 「没能自证」）时，GT 的统一冷却一翻篇就作废 ——
    # 否则「限流中」的旧结论会把同一个池子一直顶成「没有 symbol」，直到进程重启（真机事故）。
    if _gt_rate_recovered("geckoterminal") and _gt_choice_blocked(out):
        with _GT_SELF_LOCK:
            _GT_SELF_CACHE.pop(key, None)
        return None
    return out


def _gt_choice_blocked(choice):
    """这份自证结论是不是**建在 rate / down 上**（`sides[].kind` 是自证那一枪的结论，
    `sides[].symbol_kind` 是 `/tokens/{代币}` 那一枪的结论）。

    改动①：**symbol 那一枪**被限流挡住也算 —— 不然这份「确定回答」会缓存 10 分钟，
    这 10 分钟里同一个池子永远答不出 symbol（真机事故的另一半：不是熔断器，是缓存）。"""
    for s in _list(_dict(choice).get("sides")):
        s = _dict(s)
        if (_str(s.get("kind")) in GT_SELF_BLOCKED_KINDS
                or _str(s.get("symbol_kind")) in GT_SELF_BLOCKED_KINDS):
            return True
    return False


def _gt_self_put(key, choice, now=None):
    """自证结论进内存缓存。TTL 看结论建在什么上：rate / down → 60s，其余 → 10 分钟。"""
    if not key:
        return None
    now = time.monotonic() if now is None else now
    ttl = GT_SELF_RATE_TTL if _gt_choice_blocked(choice) else GT_SELF_TTL
    with _GT_SELF_LOCK:
        for k in [k for k, exp in _GT_SELF_CACHE.items() if exp[0] <= now]:
            _GT_SELF_CACHE.pop(k, None)
        _GT_SELF_CACHE[key] = (now + ttl, copy.deepcopy(choice))
    return ttl


def _gt_self_drop_blocked():
    """GT 恢复（半开探测成功 / 上游又答了话）→ 立刻丢掉「没能自证」那些 rate / down 结论。

    改动①：这就是「不许必须重启进程才能恢复」的另一半 —— 熔断器关了、缓存里的限流结论也一起清，
    同一件事马上能重新问出来（**确定回答**照旧留着：404 / 200 空那是真结论，10 分钟不用重问）。"""
    with _GT_SELF_LOCK:
        for k in list(_GT_SELF_CACHE):
            rec = _GT_SELF_CACHE[k]
            choice = _dict(rec[1]) if isinstance(rec, (tuple, list)) and len(rec) > 1 else {}
            if _gt_choice_blocked(choice):
                _GT_SELF_CACHE.pop(k, None)


def _gt_self_reset():
    """测试用：清空自证缓存（只放内存，进程退出即消失）。"""
    with _GT_SELF_LOCK:
        _GT_SELF_CACHE.clear()


def _gt_pool_degraded_choice(sides):
    """改动④降级：**没有任何一侧自证通过**，但有一侧是 rate / down（没能问出答案）。

    「没能自证」≠「没有池子」→ 保命优先：接受池子对象里**明写**的那一侧（base → quote 顺序固定）。
    ⛔ 只认 relationships 里的地址（那是真机字段，不是猜的）；池名 / 池子地址一律不参与。"""
    for cand in _list(sides):
        if _str(_dict(cand).get("kind")) in GT_SELF_BLOCKED_KINDS:
            return cand
    return None


# ---- 改动（2026-09-16 真机事故）：EVM 池子→代币用**链上 RPC 直读**（token0/token1/symbol）----
# 真机：GT 一被限流，池子→代币就退化到别的源并**认错币**（把报价侧 4Stock 当基础代币 BNC4）
# → 拿错 symbol 去拼币安、去 GT 找错池子，全线失败。池子合约自己就能回答，纯 eth_call、确定性。
# ⛔ 必须**直连**（复用 _DIRECT_OPENER）：Clash 会把这批 RPC 连接 RESET。
_EVM_RPC_URLS = {
    "bsc": ("https://bsc-rpc.publicnode.com", "https://bsc.drpc.org",
            "https://bsc.meowrpc.com", "https://1rpc.io/bnb"),
    "eth": ("https://eth-rpc.publicnode.com", "https://rpc.ankr.com/eth",
            "https://eth.llamarpc.com"),
    "base": ("https://base-rpc.publicnode.com", "https://mainnet.base.org",
             "https://1rpc.io/base"),
}
_EVM_RPC_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://geckoterminal.com",
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
}
_EVM_POOL_SIGS = (("token0", "0x0dfe1681"), ("token1", "0xd21220a7"))
_EVM_TOKEN_SIGS = (("symbol", "0x95d89b41"), ("name", "0x06fdde03"), ("decimals", "0x313ce567"))
# 已知报价/基础货币地址（小写）——用来判断哪一侧是「基础代币」
_EVM_QUOTE_ADDRS = {
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",  # WBNB
    "0x55d398326f99059ff775485246999027b3197955",  # USDT (BSC)
    "0xe9e7cea3dedca5984780bafc599bd69add087d56",  # BUSD
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",  # USDC (BSC)
    "0x2170ed0880ac9a755fd29b2688956bd959f933f8",  # ETH (BSC)
    "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c",  # BTCB
    "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82",  # CAKE
    "0x1af3f329e8be154074d8769d1ffa4ee058b1dbc3",  # DAI (BSC)
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",  # WETH
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC (ETH)
    "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT (ETH)
    "0x6b175474e89094c44da98b954eedeac495271d0f",  # DAI (ETH)
    "0x4200000000000000000000000000000000000006",  # WETH (Base)
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC (Base)
}
_EVM_CALL_BUDGET_SEC = 10.0
_EVM_RPC_TIMEOUT_SEC = 6.0
# 池子探测总上限（秒）：没这个上限的话，“指代币地址却被当池子探”会挨个试 4 条链，白花 13~17s。
_EVM_POOL_PROBE_TOTAL_SEC = _env_float("EVM_POOL_PROBE_TOTAL_SEC", 4.0)  # 池子探测总上限（秒）


def _evm_chain_of(net):
    """GT 的 network 名 → 我们的 EVM RPC 表键；不是这几条 EVM 链 → None。"""
    return {"bsc": "bsc", "eth": "eth", "ethereum": "eth", "base": "base"}.get(_str(net) or "")


def _evm_call(chain, to, data, deadline=None):
    """一次 eth_call（**直连**、轮换端点）→ (result_hex, why)。失败 why 说清，绝不猜。"""
    urls = _EVM_RPC_URLS.get(_str(chain) or "")
    if not urls:
        return None, "没有 %s 的链上 RPC 端点" % (chain or "?")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                       "params": [{"to": to, "data": data}, "latest"]}).encode()
    last = None
    for url in urls[:3]:
        if deadline is not None and time.time() > deadline:
            return None, "超出链上直读预算"
        try:
            req = urllib.request.Request(url, data=body, headers=_EVM_RPC_HEADERS)
            with _DIRECT_OPENER.open(req, timeout=_EVM_RPC_TIMEOUT_SEC) as resp:
                d = json.loads(resp.read().decode("utf-8", "replace"))
            if _dict(d).get("error"):
                last = str(_dict(d).get("error"))[:80]
                continue
            res = _str(_dict(d).get("result"))
            if res is None:
                last = "RPC 没给 result"
                continue
            return res, None
        except Exception as e:          # noqa: BLE001 —— 网络层什么都可能抛
            last = "%s: %s" % (type(e).__name__, str(e)[:70])
    return None, last or "链上 RPC 全部失败"


def _evm_word_addr(word_hex):
    """eth_call 返回值 → 地址（取末 40 位）。"""
    h = (_str(word_hex) or "").replace("0x", "")
    return ("0x" + h[-40:].lower()) if len(h) >= 40 else None


def _evm_abi_str(word_hex):
    """eth_call 返回值 → 字符串（兼容 bytes32 与动态 string 两种编码）。"""
    h = (_str(word_hex) or "").replace("0x", "")
    if not h:
        return None
    try:
        raw = bytes.fromhex(h)
    except ValueError:
        return None
    if len(raw) == 32:                      # bytes32
        return raw.rstrip(b"\x00").decode("utf-8", "replace") or None
    if len(raw) >= 64:                      # 动态 string
        ln = int.from_bytes(raw[32:64], "big")
        if 0 < ln <= len(raw) - 64:
            return raw[64:64 + ln].decode("utf-8", "replace") or None
    return None


def _evm_token_meta(chain, addr, deadline):
    """一个 ERC20 的 symbol / name / decimals（读不出就 None，绝不编）。"""
    out = {"address": addr, "symbol": None, "name": None, "decimals": None}
    for sig, data in _EVM_TOKEN_SIGS:
        hexv, _why = _evm_call(chain, addr, data, deadline)
        if hexv is None:
            continue
        if sig == "decimals":
            try:
                out["decimals"] = int(hexv, 16)
            except ValueError:
                pass
        else:
            out[sig] = _evm_abi_str(hexv)
    return out


def _evm_sides_text(sides):
    """两侧写成一行，给 notes 用（token0=BNC4、token1=4Stock）。"""
    parts = []
    for i, s in enumerate(_list(sides)):
        d = _dict(s)
        parts.append("token%d=%s" % (i, _str(d.get("symbol")) or _str(d.get("address")) or "?"))
    return "、".join(parts) or "两侧读不出"


def _evm_is_quote(addr):
    return (_str(addr) or "").lower() in _EVM_QUOTE_ADDRS


def _evm_pool_sides(net, pool):
    """池子 → 两侧代币（链上直读）→ {"sides": [...], "why": ...}（失败 why 说清，绝不猜）。

    5 个 eth_call 是互相独立的读，**并发**发出去（结果按调用序号绑定，与完成顺序无关）：
    先用一个探测并发拿 token0/token1（这俩决定是不是池子），再并发读两侧的
    symbol/name/decimals。单链 ~2s 落回总量上限内，不再被硬截断。
    """
    chain = _evm_chain_of(net)
    if not chain:
        return None
    deadline = time.time() + _EVM_CALL_BUDGET_SEC
    sigs = list(_EVM_POOL_SIGS)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(sigs) or 1) as ex:
        probes = list(ex.map(lambda sd: _evm_call(chain, pool, sd[1], deadline), sigs))
    addrs, why = [], []
    for (sig, _data), (hexv, err) in zip(sigs, probes):
        addr = _evm_word_addr(hexv)
        if not addr:
            why.append("%s() 读不出（%s）" % (sig, err or "返回值不对"))
            continue
        addrs.append(addr)
    sides = []
    if addrs:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(addrs)) as ex:
            sides = list(ex.map(lambda a: _evm_token_meta(chain, a, deadline), addrs))
    if not sides:
        return {"sides": [], "why": "；".join(why) or "链上直读失败"}
    return {"sides": sides, "why": (u"；".join(why) or None), "chain": chain}


def _evm_pick_base(sides, gt_base_addr=None):
    """哪一侧算「基础代币」：① 排除已知报价代币 ② GT 的 base_token ③ token0。→ (idx, 依据)"""
    lst = [_dict(s) for s in _list(sides) if _dict(s).get("address")]
    if not lst:
        return -1, None
    quotes = [_evm_is_quote(d.get("address")) for d in lst]
    if len(lst) == 2 and quotes.count(True) == 1:
        return quotes.index(False), "另一侧是已知报价代币"
    if gt_base_addr:
        for i, d in enumerate(lst):
            if _addr_same(_str(d.get("address")), _str(gt_base_addr), False):
                return i, "与 GeckoTerminal 的 base_token 一致"
    return 0, "token0（链上约定）"


def _gt_side_blocked(sides):
    """GT 侧有没有 rate/down 分档（= GT 没能确认这个地址的身份）→ 有就得以链上直读为准。"""
    for cand in _list(sides):
        if _str(_dict(cand).get("kind")) in GT_SELF_BLOCKED_KINDS:
            return True
    return False


def _evm_pool_fallback(net, own, sides=None):
    """池子 → 链上直读补一枪（GT 没答话 / 缓存里没认出代币时的确定性兜底）。

    复用 _evm_pool_sides / _evm_pick_base / _evm_sides_text / _is_lp_token_name，
    逻辑与 _gt_pool_token_choice 里那段**完全一致**（两处共用，别复制两份）。
    → {"chosen": chosen 字典（via="evm_rpc"）或 None, "why": 失败原因或 None}；
    `sides` 是 GT 那两侧（用来取 base_token 提示，可省）。
    """
    ev = _evm_pool_sides(net, own)
    ev_sides = _list(_dict(ev).get("sides")) if ev else []
    if not ev_sides:
        return {"chosen": None, "why": _str(_dict(ev).get("why")) or "链上直读两侧都读不出"}
    gt_hint = None
    for cand in _list(sides):
        if _str(_dict(cand).get("side")) == "base":
            gt_hint = _str(_dict(cand).get("address"))
            break
    idx, rule = _evm_pick_base(ev_sides, gt_hint)
    pick = _dict(ev_sides[idx]) if 0 <= idx < len(ev_sides) else {}
    sym_ev = _sym(pick.get("symbol"))
    if not sym_ev:
        return {"chosen": None, "why": "链上直读没能读出 token0/token1 的 symbol"}
    if _is_lp_token_name(sym_ev, pick.get("name")):
        return {"chosen": None, "why": "链上直读命中 LP 黑名单（%s）" % sym_ev}
    chosen = {
        "side": ("base" if idx == 0 else "quote"),
        "label": ("base_token" if idx == 0 else "quote_token"),
        "address": _str(pick.get("address")),
        "kind": "ok", "pools_ok": True, "lp": False, "usable": True,
        "symbol": sym_ev, "name": _str(pick.get("name")),
        "symbol_why": None, "symbol_kind": None, "why": None,
        "via": "evm_rpc", "evm_sides": _evm_sides_text(ev_sides),
        "evm_rule": rule,
    }
    return {"chosen": chosen, "why": None}


def _evm_pool_fallback_note(net, own, sides=None):
    """链上直读的结论 + 失败原因（给降级结论追加 why 用）→ (chosen, why)。"""
    fb = _evm_pool_fallback(net, own, sides)
    return _dict(fb).get("chosen") or None, _str(_dict(fb).get("why")) or None


def _gt_pool_token_choice(pool, deadline=None):
    """池子 relationships 的两侧 → 逐个**自证** → 最终代币（**固定 base → quote**，确定性）。

    → {"sides": [{side,label,address,kind,pools_ok,lp,usable,symbol,name,why}, …],
       "chosen": 那一侧或 None, "retry_after_s": 上游给的退避秒数或 None}
    自证 = `/networks/{net}/tokens/{代币}/pools?page=1`，结论**必须分类**（改动④）：
      · ok 且非空 → 自证通过（这一侧的 symbol 取其 `/tokens/{代币}`）
      · nodata（200 空）/ notfound（404）→ **确定回答：真没有池子** → 试另一侧
      · rate（429 / 节流 / 熔断）/ down（超时·5xx·连不上）→ **不确定，绝不当「没有池子」**：
        没有任何一侧自证通过时按 `_gt_pool_degraded_choice` 降级接受那一侧（note 里如实写清），
        并给 symbol 补一枪 `/tokens/{代币}`（拿不到就 null + 说明，**绝不拿池名顶替**）。
        ⚠️ **base 侧**是 rate / down 时就**直接降级**（不再拿 quote 去撞同一个 429 —— 省额度；
        口径与「两侧都限流时按 base 处理」一致）；只有 base 拿到了**确定回答**（空 / 404）才轮到 quote。
    symbol / name 一律取 `/networks/{net}/tokens/{代币}`（**池名一律不用**）；
    symbol / name 命中 LP 黑名单 → 这一侧**不算可用**（继续看另一侧）。
    结论按 (链, 池子) 缓存（改动⑤：确定回答 10 分钟、rate/down 60 秒）。
    ⚠️ 沙箱无网：真实自证请求与额度表现**需 host 真机验证**（离线用例全部打桩）。"""
    net = _gt_search_net(pool)
    own = _gt_pool_own_addr(pool)
    key = _gt_self_key(net, own)
    hit = _gt_self_get(key)
    if hit is not None:
        hit["cached"] = True            # 诊断用：这份结论是缓存来的（同一件事不重复打）
        cached_chosen = _dict(hit.get("chosen"))
        if (cached_chosen and _sym(cached_chosen.get("symbol"))
                and not _gt_side_blocked(_list(hit.get("sides")))):
            # 缓存里有可用 symbol **且 GT 当时确认过身份** → 直接用（省额度）。
            # GT 未确认身份（rate/down）时那份 symbol 可能来自对子行/报价侧，是错的 → 往下走链上直读纠正。
            return hit
        # 缓存里的 chosen 是 None（上次没认出代币）**或拿到但 symbol 为空**（真机：GT 429 降级出来的
        # base_token，地址对、symbol=None）→ 补一枪链上直读，成功就替换掉那份结论并写回缓存。
        # 否则限流后 60 秒内用户查这个池子地址还是认不出（真机事故）。
        ev_chosen, why = _evm_pool_fallback_note(net, own, _list(hit.get("sides")))
        if ev_chosen is not None:
            hit["chosen"] = ev_chosen
            _gt_self_put(key, hit)      # 写回缓存（chosen 带 symbol 后即走上面的快路）
            hit["cached"] = True
            return hit
        if not cached_chosen:
            # 链上直读也失败 → 原样返回缓存（chosen: None），**不许编造**；
            # 把失败原因追加进 why（两侧都没 why 时落 notes 来源字段）。
            added = False
            for cand in _list(hit.get("sides")):
                cand = _dict(cand)
                cand["why"] = ((_str(cand.get("why")) + "；") if _str(cand.get("why")) else "") + why
                added = True
            if not added:
                hit["notes"] = ((_str(hit.get("notes")) + "；") if _str(hit.get("notes")) else "") + why
        else:
            # 缓存里那份降级结论有不带 symbol 的那一侧 → 把链上直读失败原因如实写进那一侧的 why
            side = _str(cached_chosen.get("side"))
            for cand in _list(hit.get("sides")):
                cand = _dict(cand)
                if _str(cand.get("side")) == side:
                    cand["why"] = ((_str(cand.get("why")) + "；") if _str(cand.get("why")) else "") + why
                    break
            nulls = hit.get("null_fields")
            if isinstance(nulls, dict):
                nulls["symbol"] = ((_str(nulls.get("symbol")) + "；") if _str(nulls.get("symbol")) else "") + why
        return hit
    spec = _gt_pool_side_addrs(pool)
    if not spec:
        # /search/pools 那条读不出 base/quote → 用改动①指定的端点再问一次这个池子（同一份 relationships）
        fetched, _why = _gt_pool_by_address(net, own, deadline)
        if fetched is not None:
            net = _gt_search_net(fetched) or net
            spec = _gt_pool_side_addrs(fetched)
    sides = [dict(s, kind=None, pools_ok=False, lp=False, usable=False, symbol=None, name=None,
                  symbol_why=None, why=None) for s in spec]
    if not net:
        for cand in sides:
            cand["why"] = "池子的 data[].id 里拆不出 network → 没法自证（不猜链）"
        return {"sides": sides, "chosen": None, "retry_after_s": None}
    chosen, retry_after_s = None, None
    for cand in sides:                 # 固定顺序：base 先 → 结果与请求先后 / 完成顺序无关
        meta = {}
        pools, err, kind = _gt_token_pools(net, cand["address"], deadline, meta)
        got = _num(meta.get("retry_after_s"))
        if got is not None:
            retry_after_s = got
        cand["kind"] = kind
        if not pools:
            # 改动④：空 / 404 是「确定回答」；429 / 超时是「没能问出答案」—— 分开写，别混成一句
            cand["why"] = err or "GeckoTerminal 查不到这个代币的池子"
            if not chosen and kind in GT_SELF_BLOCKED_KINDS and cand is sides[0]:
                # base 侧压根没答话（限流 / 连不上）→ **直接按 base_token 降级**，不再拿另一侧
                # 去撞同一个 429（省额度；口径本来也是「两侧都限流时按 base 处理」，见改动④策略 2）
                break
            continue
        cand["pools_ok"] = True        # 自证通过
        sym, name, _iwhy, _ikind = _gt_token_info(net, cand["address"], deadline)
        cand["symbol"], cand["name"] = sym, name
        # symbol 那一枪的结论也留着：改动③ 的额度用尽（或那一枪 429 / 连不上）时要如实说明
        cand["symbol_why"], cand["symbol_kind"] = _iwhy, _ikind
        if _is_lp_token_name(sym, name):
            cand["lp"] = True
            cand["why"] = "symbol/name 命中 LP 黑名单（%s）" % (_str(sym) or _str(name) or "?")
            continue
        cand["usable"] = True
        cand["why"] = None
        chosen = cand
        break
    ev_why = None
    if (chosen is None or not _sym(chosen.get("symbol"))
            or _gt_side_blocked(sides)):
        # 第三种情况（真机事故）：降级候选**有** symbol 但 GT 没确认身份 → 那个 symbol 可能是
        # 对子行的 base/报价侧（如 4Stock）→ 必须以链上直读为准纠正。
        # 改动（真机）：GT 没能答话 / 认不出，**或者降级出来的那一侧没拿到 symbol**
        # （429 时 chosen 是 base_token 降级结论：地址对、symbol=None）→ **链上直读**补一枪
        # （确定性：token0/token1/symbol）。这一步不依赖 GT / GMGN，认币绝不靠池名。
        # 读出来了 → **替换** chosen（保留 via="evm_rpc" / evm_sides / evm_rule 字段，side 按 _evm_pick_base）。
        # 读不出来 → 保持原 chosen 不变（**不许编造**），只把失败原因追加进对应 side 的 why。
        # ⚠️ 「连读都没读出来」的 chosen=None 情形留给下面 _gt_pool_degraded_choice 先降级，
        # 从这里替换不到东西才轮到它（保证降级口径一字不改）。
        ev_chosen, ev_why = _evm_pool_fallback_note(net, own, sides)
        if ev_chosen is not None:
            chosen = ev_chosen
    if chosen is None:
        chosen = _gt_pool_degraded_choice(sides)
    if chosen is None:
        # 失败文案要「附两侧地址与 symbol」→ 还没读到 symbol 的那侧补一发（拿不到就写「拿不到」）
        for cand in sides:
            if not cand["symbol"] and not cand["name"]:
                sym, name, why, ikind = _gt_token_info(net, cand["address"], deadline)
                cand["symbol"], cand["name"] = sym, name
                cand["symbol_why"], cand["symbol_kind"] = why, ikind
    elif _str(chosen.get("kind")) in GT_SELF_BLOCKED_KINDS and not chosen["symbol"]:
        # 降级接受的那一侧：symbol 仍只问 /tokens/{代币}；拿不到（限流）→ null + 说明
        sym, name, why, ikind = _gt_token_info(net, chosen["address"], deadline)
        chosen["symbol"], chosen["name"] = sym, name
        chosen["symbol_why"], chosen["symbol_kind"] = why, ikind
    if (ev_why and _dict(chosen) is not None
            and not _sym(_dict(chosen).get("symbol"))
            and _str(_dict(chosen).get("via")) != "evm_rpc"):
        # 链上直读试过但失败、最终仍走 GT 降级 → 失败原因如实写进该 side 的 why
        for cand in sides:
            cand = _dict(cand)
            if _str(cand.get("side")) == _str(_dict(chosen).get("side")):
                cand["why"] = ((_str(cand.get("why")) + "；") if _str(cand.get("why")) else "") + ev_why
                break
    out = {"sides": sides, "chosen": chosen, "retry_after_s": retry_after_s}
    if sides:            # 两侧都没读出来（relationships 里啥都没有）→ 没有结论可缓存
        _gt_self_put(key, out)
    return out


def _gt_pool_sides_note(own, net, sides):
    """两侧都不能用时的人话（**附两侧地址与 symbol**，并写清各自为什么没通过）。

    改动④：只有「两侧都拿到了**确定回答**（200 空 / HTTP 404）」时才允许说「都查不到池子」，
    而且必须在同一句里写明「本次拿到了 GeckoTerminal 的确定回答，不是限流」——
    rate / down 那条路根本走不到这里（它降级接受 base_token）。"""
    both_empty = not any(_dict(c).get("pools_ok") for c in _list(sides))
    if both_empty:
        head = "这个池子的两侧代币在 GeckoTerminal 都查不到池子"
    else:
        head = "这个池子的两侧代币都不是可用代币（另一侧 / LP 代币）"
    shown = "；".join(
        "%s %s（%s）" % (_dict(c).get("label"), _dict(c).get("address"),
                        _str(_dict(c).get("symbol")) or _str(_dict(c).get("name")) or "symbol 拿不到")
        for c in _list(sides))
    why = "；".join("%s %s" % (_dict(c).get("label"), _dict(c).get("why") or "自证没通过")
                    for c in _list(sides))
    certain = (bool(_list(sides)) and all(_str(_dict(c).get("kind")) in GT_SELF_EMPTY_KINDS
                                          for c in _list(sides)))
    tail = ("；本次拿到了 GeckoTerminal 的确定回答（HTTP 404 / 空），不是限流"
            if (both_empty and certain) else "")
    return ("%s：%s（池子 %s%s）。两侧都没自证通过 → 不给代币结果"
            "（绝不拿池名 / LP 代币 / 池子地址冒充）%s"
            % (head, shown or "两侧地址都读不出来（relationships 里没有 base/quote）",
               own or "（地址读不出）", "，链 %s" % net if net else "",
               ("；" + why + tail) if why else tail))


# 改动④降级文案：自证被 **rate / down** 挡住 ≠「没有池子」→ 按池子里明写的那一侧处理（如实标注）。
_GT_SELF_DEGRADE_TEXT = {
    "rate": "GeckoTerminal 限流中（HTTP 429），未能自证；按池子的 %s 处理",
    "down": "GeckoTerminal 没答话（超时 / 5xx / 连不上），未能自证；按池子的 %s 处理",
}
# symbol 那一发的说明（只认 /tokens/{代币}；**绝不拿池名顶替**）
_GT_SYMBOL_MISS_TEXT = {
    "rate": "symbol 因限流暂缺（只认 GeckoTerminal /tokens/{代币}，绝不拿池名顶替）",
    "down": "symbol 因 GeckoTerminal 没答话暂缺（只认 /tokens/{代币}，绝不拿池名顶替）",
}


def _gt_self_degrade_note(side, kind, sym, symbol_kind=None, symbol_why=None):
    """降级那一侧的人话：说清「哪一级没通过 + 按哪一侧处理 + symbol 为什么缺」。"""
    label = "base_token" if side == "base" else "quote_token"
    head = _GT_SELF_DEGRADE_TEXT.get(kind) or _GT_SELF_DEGRADE_TEXT["rate"]
    out = head % label
    if sym:
        out += "（symbol %s）" % sym
        return out
    out += "；" + (_GT_SYMBOL_MISS_TEXT.get(_str(symbol_kind))
                   or ("symbol 拿不到（%s）" % (symbol_why or "原因拿不到")))
    return out


# ---- 改动②（2026-09-16 真机）：symbol 不许**无谓**留空 ----
# 真机：GT 的 /tokens/{代币} 被限流挡住 → 行里 symbol 直接 null（哪怕这次查询里别的源早就
# 报出过这个代币的 symbol）。现在取值顺序固定成：
#   ① GT `/networks/{net}/tokens/{代币}`（权威）
#   ② 拿不到（rate / down / 额度）→ 退到**本次查询里已经拿到的其它源**：
#      DexScreener 的 `baseToken.symbol` / GMGN search 的 symbol（没调用过就没有这一档）
#   ③ 都没有 → **保持 null + 原因**（照旧，绝不用池名顶替）
# ⛔ 池名 / LP 代币名（黑名单）**任何一档都不许当 symbol**（登记与取用时都过黑名单）。
# ⛔ 退路不许自己去发请求（那会烧额度）：只认「这次查询里别的源已经报出来的」symbol。
_SYM_FB = threading.local()
_SYM_FB_LABELS = {"dexscreener": "DexScreener", "gmgn_search": "GMGN search"}
_SYM_FB_ORDER = ("dexscreener", "gmgn_search")      # 多个退路都在 → DexScreener 优先（任务口径）


def _sym_fb_open():
    """开一次查询的退路登记表（thread-local）：一次 /api/lookup 的身份解析在**一个**线程里跑。
    没开表就登记 = 不登记（绝不让上一次查询的 symbol 漏进下一次）。"""
    table = getattr(_SYM_FB, "table", None)
    if table is None:
        table = _SYM_FB.table = {}
    return table


def _sym_fb_close():
    """一次查询结束 → 关表（⛔ 只放内存，绝不落盘、绝不跨查询复用）。"""
    _SYM_FB.table = None


def _sym_fb_put(source, symbol, name=None):
    """别的源报出的 symbol → 登记进**本次查询**的退路表（LP 代币名绝不登记）。"""
    table = getattr(_SYM_FB, "table", None)
    if table is None:
        return None
    key = _str(source) or "?"
    sym = _sym(symbol)
    if not sym or _is_lp_token_name(sym, name) or _is_pair_like_name(sym, name):
        return None       # LP 代币名 / 对子名（池名）一律不登记（改动④）
    rec = {"source": key, "label": _SYM_FB_LABELS.get(key, key), "symbol": sym}
    prev = table.get(key)
    if prev is None or (not _str(prev.get("symbol"))):
        table[key] = rec
    return rec


def _sym_fb_get():
    """本次查询已经拿到的退路 symbol → {"source","label","symbol"}；没有 → None。"""
    table = getattr(_SYM_FB, "table", None) or {}
    for key in _SYM_FB_ORDER:
        rec = table.get(key)
        if not isinstance(rec, dict):
            continue
        sym = _str(rec.get("symbol"))
        if not sym or _is_lp_token_name(sym, None) or _is_pair_like_name(sym, None):
            continue                       # 黑名单再拦一次：LP 代币名 / 对子名任何一档都不许当 symbol
        return dict(rec)
    return None


def _gt_symbol_fb_note(fb, kind):
    """退路必须写明来源（任务口径）：「symbol 取自 DexScreener（GeckoTerminal 限流中）」。"""
    why = ("GeckoTerminal 限流中" if _str(kind) != "down"
           else "GeckoTerminal 没答话（超时 / 5xx / 连不上）")
    return "symbol 取自 %s（%s）" % (_dict(fb).get("label") or _dict(fb).get("source") or "其它源", why)


def _gt_pool_token_row(row, pool, query, deadline=None):
    """改动①：用户给的是**池子** → 把这行如实改写成「按代币分析」（**只认 relationships + 自证**）。

    成功：kind="token"、token_address = 自证通过那一侧的地址、symbol / name **取该代币自己的**
    （GT `/networks/{net}/tokens/{代币}`；池名一律不用），notes 写
    「你给的是池子 <0x…>（链 bsc），已按基础代币 <0x…>（BNC4）分析」，池子地址留在 pair_address。
    两侧都不可用：kind="lp_pool"、token_address / symbol 都 null + notes 与 `gt_blocked` 写清
    「这个池子的两侧代币在 GeckoTerminal 都查不到池子」（附两侧地址与 symbol）—— **绝不猜**，
    也**不许**下游拿池子地址 / LP 代币去发 GT 日线（改动③）。
    改动④（真机事故：GT 429 被当成了「空」）：自证那一枪是 **rate / down**（没能问出答案、不是
    「没有池子」）→ **降级接受池子里明写的 base_token**（保命优先），但必须**诚实标注**：
    notes 写「GeckoTerminal 限流中（HTTP 429），未能自证；按池子的 base_token 处理」
    （超时 / 5xx / 连不上同理），行里带结构化 `blocked_by=[{source:geckoterminal, kind, retry_after_s}]`；
    symbol 仍只取 `/tokens/{代币}`，取不到（限流）→ null + 「symbol 因限流暂缺」，**绝不拿池名顶替**。
    → (row, note)：note 是给 missing 的那句口径说明（成功 / 失败都有）。"""
    r = _dict(row)
    net = _str(r.get("chain_id")) or _gt_search_net(pool)
    own = _gt_pool_own_addr(pool)
    choice = _gt_pool_token_choice(pool, deadline)
    sides, chosen = choice["sides"], choice["chosen"]
    nulls = r.get("null_fields") if isinstance(r.get("null_fields"), dict) else {}
    if chosen is None:
        msg = _gt_pool_sides_note(own, net, sides)
        r["kind"] = "lp_pool"
        r["token_address"] = None
        r["symbol"] = None              # **池名一律不拿来当 symbol**（改①：池名只是文字）
        # name 是**显示标签**（允许保留池名 / 对子名，如 "BONK / SOL"）—— 这次收紧的只有 symbol：
        # 池名一个字符都不许进 symbol，但 name 照旧保留（面板上要看得出用户给的是哪个池子）。
        r["name"] = _str(_dict(_dict(pool).get("attributes")).get("name")) or r.get("name")
        r["notes"] = [msg]
        r["gt_blocked"] = msg           # 改动③：代币没定 → 不发 GT 日线（也不拿池子地址顶替）
        nulls.pop("kind", None)
        nulls["token_address"] = msg
        nulls["symbol"] = "两侧代币都没自证通过 → 池名 / LP 代币都不拿来当 symbol"
        r["null_fields"] = nulls
        return r, msg
    r["kind"] = "token"
    r["token_address"] = chosen["address"]
    r.pop("gt_blocked", None)           # 代币定了 → 改动③ 那道拦阻必须摘掉
    nulls.pop("kind", None)
    nulls.pop("token_address", None)
    degraded = _str(chosen.get("kind")) in GT_SELF_BLOCKED_KINDS
    sym = _sym(chosen["symbol"]) if chosen["symbol"] else None
    # 改动②：GT 的 /tokens/{代币} 这一枪不可用（rate / down / 额度）→ 退到本次查询的其它源。
    # 只在「那一枪压根没答话」时退：GT 明确回了「没有 symbol」就不退（那是确定回答，绝不覆盖）。
    sym_blocked = degraded or _str(chosen.get("symbol_kind")) in GT_SELF_BLOCKED_KINDS
    fb = _sym_fb_get() if (not sym and sym_blocked) else None
    fb_note = (_gt_symbol_fb_note(fb, chosen.get("kind") or chosen.get("symbol_kind"))
               if fb else None)
    if sym:
        r["symbol"] = sym
        if _str(chosen.get("via")) == "evm_rpc":
            r["symbol_source"] = "evm_rpc"      # 结构化地说清这个 symbol 是链上直读给的
        nulls.pop("symbol", None)
    elif fb:
        r["symbol"] = fb["symbol"]
        r["symbol_source"] = fb["source"]          # 结构化地说清这个 symbol 是谁给的
        nulls.pop("symbol", None)                  # symbol 有着落了 → 不许再挂在「缺失」里
        r["symbol_note"] = fb_note                 # 来源也留一份结构化的（面板照抄 notes 那句）
    elif degraded:
        # 降级：自证没做成（限流 / 连不上）→ symbol 只认 /tokens/{代币} 的答复，绝不拿池名顶替
        r["symbol"] = None
        nulls["symbol"] = _gt_self_degrade_note(chosen["side"], chosen["kind"], None,
                                                chosen.get("symbol_kind"), chosen.get("symbol_why"))
    else:
        r["symbol"] = None
        if _str(chosen.get("symbol_kind")) in GT_SELF_BLOCKED_KINDS:
            # 改动③：symbol 那一枪被限流 / 额度上限挡住（自证本身是通的）→ 如实写原因
            nulls["symbol"] = ("symbol 那一枪没答话（%s）→ 记 null，不拿池名顶替"
                               % (chosen.get("symbol_why") or "限流 / 额度上限"))
            if _gt_cooling("geckoterminal"):
                # 改动①③：GT **此刻**真的在冷却窗口里 → 结构化 blocked_by 如实带上
                # （冷却一过 `_rows_blocked_by` 就会把它摘掉 —— 只报当前状态，不报历史）
                r["blocked_by"] = [{"source": "geckoterminal",
                                    "kind": _str(chosen.get("symbol_kind")),
                                    "retry_after_s": _ra_out(choice.get("retry_after_s"))}]
        else:
            nulls["symbol"] = ("GeckoTerminal token 接口没给 symbol（自证已经过了）→ 记 null，"
                               "不拿池名顶替")
    r["name"] = chosen["name"] or sym or (fb["symbol"] if fb else None)
    r["null_fields"] = nulls
    if degraded:
        # 改动④：自证被 rate / down 挡住 → 按池子里明写的那一侧处理，notes 必须**写明是限流没自证**
        note = _gt_self_degrade_note(chosen["side"], chosen["kind"],
                                     sym or (fb["symbol"] if fb else None),
                                     chosen.get("symbol_kind"), chosen.get("symbol_why"))
        r["blocked_by"] = [{"source": "geckoterminal", "kind": chosen["kind"],
                            "retry_after_s": _ra_out(choice.get("retry_after_s"))}]
    elif chosen["side"] == "base" and _str(chosen.get("via")) == "evm_rpc":
        # 链上直读认出了代币 → 必须把**两侧**都写出来，用户能看到面板是怎么选的
        note = ("你给的是池子 %s（链 %s）；链上直读：%s；已按基础代币 %s（%s）分析（选取依据：%s）"
                % (own or "（地址读不出）", net or "（认不出）",
                   chosen.get("evm_sides") or "两侧读不出", chosen["address"],
                   sym or (fb["symbol"] if fb else None) or "symbol 拿不到",
                   chosen.get("evm_rule") or "链上约定"))
        # 链上直读补上了 symbol，但**不许因此不报 GT 当时是限流的**（如实披露，只报当前状态）
        _gt_blocked_kind = None
        for _cand in sides:
            _k = _str(_dict(_cand).get("kind"))
            if _k in GT_SELF_BLOCKED_KINDS:
                _gt_blocked_kind = _k
                break
        if _gt_blocked_kind:
            note += "（GeckoTerminal 当时%s，未能自证；代币由链上直读确定）" % (
                "限流中（HTTP 429）" if _gt_blocked_kind == "rate" else "连不上")
            r["blocked_by"] = [{"source": "geckoterminal", "kind": _gt_blocked_kind,
                                "retry_after_s": _ra_out(choice.get("retry_after_s"))}]
    elif chosen["side"] == "base":
        note = ("你给的是池子 %s（链 %s），已按基础代币 %s（%s）分析"
                % (own or "（地址读不出）", net or "（认不出）", chosen["address"],
                   sym or (fb["symbol"] if fb else None) or "symbol 拿不到"))
    else:
        why = "；".join("%s %s" % (_dict(c).get("label"), _dict(c).get("why"))
                        for c in sides if c is not chosen and _dict(c).get("why"))
        note = ("你给的是池子 %s（链 %s），基础代币那一侧不可用%s → 已按报价代币 %s（%s）分析"
                % (own or "（地址读不出）", net or "（认不出）",
                   ("（%s）" % why) if why else "", chosen["address"],
                   sym or (fb["symbol"] if fb else None) or "symbol 拿不到"))
    if fb_note:
        note += "；" + fb_note            # 改动②：退路必须在面板（missing）上说清来源
    r["notes"] = [note]
    return r, note


def _gt_search_kind(pool, query, is_sol):
    """查询地址到底是谁？→ (kind, 匹配到的代币地址)
      · "token"        base_token 的地址 == 查询地址 → 查的是**代币**
      · "quote_token"  quote_token 的地址 == 查询地址 → 查的是 quote 侧那个代币
      · "pool_address" **池子自己的地址 == 查询地址**、且 base_token 是**另一个**地址 →
                       用户给的是**池子**（改动①：_gt_pool_verdict_pool / _gt_pool_token_row
                       只认 relationships + 自证；这里只是那条路之外的兜底判定）
      · "lp_pool"      base/quote 都对不上（或池子地址就是查询地址、但 relationships 读不出来）
                       → 只知道「这是个池子」，认不出代币 → 不冒充代币
      · None           relationships 里两个 id 都读不出来 → 连「代币还是池子」都不猜
    地址比较：EVM 小写归一；Solana base58 大小写敏感。"""
    base_addr = _gt_rel_token_addr(pool, "base_token")
    if base_addr and _addr_same(base_addr, query, is_sol):
        return "token", base_addr
    quote_addr = _gt_rel_token_addr(pool, "quote_token")
    if quote_addr and _addr_same(quote_addr, query, is_sol):
        return "quote_token", quote_addr
    own_addr = _gt_pool_own_addr(pool)
    if own_addr and _addr_same(own_addr, query, is_sol):
        if base_addr and not _addr_same(base_addr, query, is_sol):
            return "pool_address", base_addr
        if not (base_addr or quote_addr):
            # 查询地址**就是池子**，但 relationships 里认不出代币 → 如实标池子，不冒充代币
            return "lp_pool", None
    if base_addr or quote_addr:
        return "lp_pool", None
    return None, None


def _gt_search_row_from_pool(pool, query, is_address, kind, token_addr):
    """/search/pools 的一条池子 → 结果行。字段**只用真机核过的那些**；
    缺的一律 null + 一句原因（绝不猜、绝不拿别的数顶替）。"""
    attrs = _dict(_dict(pool).get("attributes"))
    if not attrs:
        return None
    net = _gt_search_net(pool)
    raw_created = (_str(attrs.get("pool_created_at")) or "").strip() or None
    split_sym, split_name = _gt_split_pool_name(attrs.get("name"))
    _left, sep, right = (_str(attrs.get("name")) or "").partition("/")
    quote_raw = _GT_FEE_RE.sub("", right.strip()).strip() if sep else None
    if kind == "quote_token" and quote_raw:
        # 查询地址在 quote 侧 → symbol/name 取 quote 侧（那才是「查询地址本人」的代币）
        symbol, name = _sym(quote_raw), quote_raw
    else:
        symbol, name = _sym(split_sym), split_name
    price_usd = _num(attrs.get("base_token_price_usd"))
    liq = _num(attrs.get("reserve_in_usd"))
    fdv = _num(attrs.get("fdv_usd"))
    mcap = _num(attrs.get("market_cap_usd"))
    v24 = _num(_dict(attrs.get("volume_usd")).get("h24"))
    ch24 = _num(_dict(attrs.get("price_change_percentage")).get("h24"))
    pool_addr = (_str(attrs.get("address")) or "").strip() or None
    chain_name = _gt_search_chain_name(net)
    nulls = {}
    if price_usd is None:
        nulls["price_usd"] = "该池没给 base_token_price_usd"
    if mcap is None:
        nulls["mcap_usd"] = "该池没给 market_cap_usd（不拿 fdv 顶替市值）"
    if fdv is None:
        nulls["fdv"] = "该池没给 fdv_usd"
    if liq is None:
        nulls["liquidity_usd"] = "该池没给 reserve_in_usd"
    if v24 is None:
        nulls["volume_24h"] = "该池没给 volume_usd.h24"
    if ch24 is None:
        nulls["price_change_24h"] = "该池没给 price_change_percentage.h24"
    if raw_created is None:
        nulls["pool_created_at"] = "该池没给 pool_created_at"
        nulls["pair_created_at"] = "该池没给 pool_created_at（ISO 时间戳）→ 日期也算不出来"
    if pool_addr is None:
        nulls["pair_address"] = "该池没给 attributes.address"
    if net is None:
        nulls["chain_id"] = "data[].id 不是 \"<network>_<address>\" 形状，链名认不出（不硬编）"
        nulls["chain"] = "data[].id 不是 \"<network>_<address>\" 形状，链名认不出（不硬编）"
    if not symbol:
        nulls["symbol"] = "attributes.name 里拆不出 \" / \" 那一侧的符号"
    if kind is None:
        nulls["kind"] = ("relationships 里 base_token / quote_token 都读不出来，"
                         "这里不猜是代币还是池子")
    if token_addr is None:
        if kind == "lp_pool":
            nulls["token_address"] = ("查询地址是这一个池子（LP）的地址，不是代币 → "
                                      "如实标注 kind=lp_pool，不冒充代币")
        elif kind == "pool_hit":
            nulls["token_address"] = "名称搜索命中的是池子，没有「查询地址本人」这个代币"
        elif kind is None:
            nulls["token_address"] = "relationships 读不出来，认不出代表查询地址的那个代币"
    nulls["id"] = "GeckoTerminal 不提供 CoinGecko id"
    nulls["market_cap_rank"] = "GeckoTerminal 不提供市值排名"
    nulls["drawdown_from_ath_pct"] = "GeckoTerminal 不提供距ATH（CoinGecko 能补就补）"
    nulls["ret_7d_pct"] = nulls["ret_30d_pct"] = nulls["ret_1y_pct"] = \
        "GeckoTerminal 池子接口只给 24h 涨跌，不给 7d/30d/1y"
    base = {
        "id": None,          # GeckoTerminal 没有 CoinGecko id；补不到就 null，绝不拿地址冒充
        "symbol": symbol,
        "name": name,
        "market_cap_rank": None,
        "price_usd": price_usd,
        "drawdown_from_ath_pct": None,
        "ret_7d_pct": None, "ret_30d_pct": None, "ret_1y_pct": None,
        "mcap_usd": mcap,
        "vol24h_usd": v24,
        "chain_id": net,
        "chain_name": chain_name,
        # 链名就藏在 data[].id 的 "<network>_<地址>" 前缀里（net）→ 结果行**必须**带上，
        # 下游「按链 + 合约地址取日线」（GMGN / GeckoTerminal /tokens/…/pools）全靠它。
        "chain": net,
        "dex_id": _gt_dex_id(pool),
        "address": ((_str(query) or "").strip() or None) if is_address else None,
        "pair_address": pool_addr,
        "liquidity_usd": liq,
        "fdv": fdv,
        "volume_24h": v24,
        "price_change_24h": ch24,
        "pair_created_at": _iso_date_only(raw_created),
        "pool_created_at": raw_created,
        "kind": kind,
        "token_address": token_addr,
        "null_fields": nulls,
    }
    if is_address and token_addr is None:
        # 改动③：地址查询里**定不出代币**（kind=lp_pool / relationships 读不出）→ 这行的 symbol
        # 只是**池名**的一侧，拿它（或池子地址）去发 GT 日线只会白烧额度 + 误报 404 → 拦住。
        base["gt_blocked"] = ("用户给的是地址、但没能定出代币（kind=%s）→ 不拿池子地址 / 池名 "
                              "去发 GeckoTerminal 日线" % (kind or "认不出"))
    row = _result_row(base, None, source=GT_SEARCH_SOURCE)
    # 链名也塞进 chains：前端那张卡「链」那一行就能显示「BSC 币安智能链」
    row["chains"] = [chain_name] if chain_name else None
    return row


def _gt_search_pools(query, is_address=True, deadline=None):
    """/search/pools 的**唯一**请求入口 → (pools, note, kind, retry_after_s)：
    pools 是原样的 data[] 列表
    （调用方自己解析链/代币——名称兜底那条路要 relationships.base_token 的地址），
    0 条 / 报错一律 (None 之外的 []) + 一句人话原因 + **结论分类**（改动①）：
      · ok      命中池子
      · nodata  HTTP 200 空 / 其它 4xx：**确定回答**「这个地址不是池子」
      · rate    429 / 本地熔断 / 额度用完：**不确定**（改动①绝对不许当成「不是池子」而收手）
      · down    连不上 / 超时 / 5xx：**不确定**，同上
    retry_after_s = 上游 429 报文里的 Retry-After（没有 / 认不出 → None，**绝不编数字**）。
    改动③：这一枪要过**本次查询的 GT 调用额度**（GT_CALLS_PER_QUERY_MAX）——
    超了就一发都不发（kind=rate），并让调用方如实标注「已达上限」。
    ⛔ **只发 1 个请求**（不再逐链探测 —— 那是兜底路径 _gt_address_rows 的事）。"""
    q = (_str(query) or "").strip()
    if not q:
        return [], "GeckoTerminal /search/pools 查询词为空（没发请求）", "nodata", None
    if _cb_is_open("geckoterminal_search"):
        return ([], "GeckoTerminal /search/pools 自己在熔断中，这次没发请求（恢复后自动重试）",
                "rate", None)
    if not _gt_call_ok():
        return [], "GeckoTerminal /search/pools 没发（%s）" % _gt_capped_text(), "rate", None
    url = URL_GT_SEARCH_POOLS % urllib.parse.quote(q)
    # 一次查询就一发，不需要 _gt_pool_slot 的「探测间隔」（那是 7 发逐链防连发用的）
    meta = {}
    data, err, code, rl = _http_json(
        url, throttled=False, deadline=deadline, opener=_GT_OPENER,
        source="geckoterminal_search",
        budget=ADDRESS_LOOKUP_BUDGET if is_address else LOOKUP_BUDGET, meta=meta)
    retry_after_s = _num(meta.get("retry_after_s"))
    pools = [p for p in _list(_dict(data).get("data")) if isinstance(p, dict)]
    if pools:
        return pools, None, "ok", retry_after_s
    if rl or code == 429:
        return [], "GeckoTerminal /search/pools 限流(429)，约 1 分钟后恢复", "rate", retry_after_s
    if code is None:
        return ([], "GeckoTerminal /search/pools 请求失败（连不上：%s）" % (err or "未知错误"),
                "down", retry_after_s)
    if code is not None and 500 <= code < 600:
        return ([], "GeckoTerminal /search/pools 查询失败（HTTP %s：%s）" % (code, err or "上游错误"),
                "down", retry_after_s)
    if err:
        return [], "GeckoTerminal /search/pools 查询失败（%s）" % err, "nodata", retry_after_s
    return ([], "GeckoTerminal /search/pools 没有「%s」的池子（一次定链）" % q,
            "nodata", retry_after_s)


def _gt_token_info(net, address, deadline=None):
    """GT /networks/{net}/tokens/{addr} → (symbol, name, 人话原因, kind)。

    只给**改动①**那条路用：用户给的是**池子地址**时，symbol/name 必须取**基础代币自己的**
    （池名 "BNC4 / 4Stock" 只有 base/quote 两段文字，不保证等于代币符号 → 绝不用池名顶替）。
    读不出来 → (None, None, why, kind)，调用方如实标注，不猜；kind 口径同 _gt_token_pools
    （ok / rate / down / nodata / notfound）—— 改动④：**限流（rate）**时调用方必须能说清
    「symbol 因限流暂缺」，绝不能把这句说成「这个币没 symbol」。
    ⚠️ 沙箱无网：这一发**需 host 真机验证**（真机 ≈1.5s；字段 attributes.symbol / name）。
    """
    n = (_str(net) or "").strip()
    a = (_str(address) or "").strip()
    if not n or not a:
        return None, None, "没有「链 + 代币地址」→ 拿不到代币 symbol（不猜）", "skip"
    if _cb_is_open("geckoterminal"):
        return None, None, "GeckoTerminal 熔断中，这一发没发（symbol 拿不到）", "rate"
    if not _gt_call_ok():      # 改动③：本次查询的 GT 额度用完了 → 停手（symbol 因额度记 null）
        return None, None, "GeckoTerminal %s（symbol 拿不到）" % _gt_capped_text(), "rate"
    if not _gt_pool_slot(deadline):
        return None, None, "GeckoTerminal 限流间隔/预算不够，这一发没发（symbol 拿不到）", "rate"
    url = URL_GT_TOKEN % (urllib.parse.quote(n), urllib.parse.quote(a))
    data, err, code, rl = _http_json(url, throttled=False, deadline=deadline,
                                     opener=_GT_OPENER, source="geckoterminal",
                                     budget=ADDRESS_LOOKUP_BUDGET)
    attrs = _dict(_dict(_dict(data).get("data")).get("attributes"))
    sym = (_str(attrs.get("symbol")) or "").strip() or None
    name = (_str(attrs.get("name")) or "").strip() or None
    if sym or name:
        return sym, name, None, "ok"
    if rl or code == 429:
        return None, None, "限流(429)", "rate"
    if code == 404:
        return None, None, "GeckoTerminal 没这个代币（HTTP 404）", "notfound"
    if code is None:
        return None, None, "连不上（%s）" % (err or "未知错误"), "down"
    return None, None, (err or "返回里没有 attributes.symbol / name"), "nodata"


def _gt_search_rows(query, deadline=None, is_address=True, pools=_GT_POOLS_UNFETCHED, note=None):
    """GeckoTerminal /search/pools **一次调用定链** → (rows, note)。
      · 链名从 data[].id 的 "<network>_<address>" 前缀取（未知 network 原样显示，绝不硬编）
      · 每个池一行，按 reserve_in_usd 降序，最多 GT_SEARCH_LIMIT 行
      · **地址查询**（is_address=True）：
          - 查询地址 == 某个池子自己的地址 → 用户给的是**池子** → **只**走改①那条路
            （_gt_pool_token_row：relationships 的 base/quote + 自证 + LP 黑名单 + 固定顺序），
            返回**只有这一行**（别的池子 / 别的源一律不看 → 结果确定，不靠顺序）；
            两侧都不自证 → 0 行 + 明确文案（调用方原样写进 missing）
          - 查询地址是某一侧（base/quote）→ 老口径：kind=token / quote_token（symbol 取池名该侧）
          - 都不是 → kind=lp_pool，如实标注、不冒充代币
      · 名称搜索（is_address=False）：每行 kind="pool_hit"（池子命中），带各自的链名
      · 缺字段一律 null + 一句原因；0 条 / 报错都写清「哪个源、什么错」
    ⛔ **只发 1 个请求**（不再逐链探测 —— 那是兜底路径 _gt_address_rows 的事）。
       `pools` / `note` 可以由调用方**已经拿到的那一枪**传进来（同一件事绝不发两次请求；
       `pools=_GT_POOLS_UNFETCHED` = 还没查过 → 这里自己发）。"""
    q = (_str(query) or "").strip()
    if not q:
        return [], "GeckoTerminal /search/pools 查询词为空（没发请求）"
    is_sol = bool(_MODE_SOL_RE.match(q)) if is_address else False
    if pools is _GT_POOLS_UNFETCHED:
        pools, note = _gt_search_pools(q, is_address, deadline)[:2]
    if not pools:
        return [], note
    if is_address:
        verdict = _gt_pool_verdict_pool(pools, q, is_sol)
        if verdict is not None:
            # **用户给的是池子** → 只认 relationships + 自证（改①）；别的池子命中一律不看
            row = _gt_search_row_from_pool(verdict, q, True, "pool_address",
                                           _gt_rel_token_addr(verdict, "base_token"))
            if row is None:
                return [], ("GeckoTerminal /search/pools 命中的池子 %s 读不出 attributes（不猜）"
                            % (_gt_pool_own_addr(verdict) or q))
            row, row_note = _gt_pool_token_row(row, verdict, q, deadline)
            return [row], row_note
    rows = []
    pool_addr_rows = []
    for pool in pools:
        if is_address:
            kind, token_addr = _gt_search_kind(pool, q, is_sol)
        else:
            kind, token_addr = "pool_hit", None
        row = _gt_search_row_from_pool(pool, q, is_address, kind, token_addr)
        if row is None:
            continue
        if kind == "pool_address":
            # 用户给的是**池子**（老口径那条路的兜底：同一地址只会命中一次 verdict，这里为防万一）
            row, _rnote = _gt_pool_token_row(row, pool, q, deadline)
            if row.get("kind") == "lp_pool":
                return [row], row.get("notes", [None])[0]
            pool_addr_rows.append(row)
        else:
            rows.append(row)
    if not rows and not pool_addr_rows:
        return [], ("GeckoTerminal /search/pools 命中 %d 条但字段一个都读不出来（不猜）"
                    % len(pools))
    # 两组各自按流动性降序；「池子地址 == 查询地址」那条**就是用户查的东西** → 整组排最前面
    rows.sort(key=_row_liq_key, reverse=True)
    pool_addr_rows.sort(key=_row_liq_key, reverse=True)
    return (pool_addr_rows + rows)[:GT_SEARCH_LIMIT], None


_GT_POOL_LOCK = threading.Lock()
_GT_POOL_LAST = [0.0]


def _gt_pool_slot(deadline=None):
    """相邻的 GeckoTerminal 请求之间串一下间隔（GT 免费额度约 30 次/分钟）。
    并行探测时 4 个线程都从这里取号 → 发车间隔仍是 GT_POOL_MIN_INTERVAL 秒：
    既并行（不互相等请求）又不把上游打成连发。
    等这次间隔会超过本次预算 → 直接放弃（返回 False），别让用户干等。"""
    with _GT_POOL_LOCK:
        wait = GT_POOL_MIN_INTERVAL - (time.monotonic() - _GT_POOL_LAST[0])
        if wait > 0:
            if deadline is not None and time.monotonic() + wait > deadline:
                return False
            time.sleep(wait)
        _GT_POOL_LAST[0] = time.monotonic()
    return True


_GT_NEG_LOCK = threading.Lock()
_GT_NEG_CACHE = {}      # (net, 归一地址) -> 过期时刻（time.monotonic()）；⛔ 只放内存，绝不落盘


def _gt_addr_key(addr):
    """负缓存 / 去重的地址归一：EVM 大小写不敏感 → 小写；base58 大小写敏感 → 原样。"""
    a = (_str(addr) or "").strip()
    return a.lower() if _MODE_EVM_RE.match(a) else a


def _gt_neg_hit(net, key, now=None):
    """这条链上「刚探过、确认没有池子」吗（GT_NEG_TTL 秒内不再重复打）。"""
    now = time.monotonic() if now is None else now
    with _GT_NEG_LOCK:
        exp = _GT_NEG_CACHE.get((net, key))
        if exp is None:
            return False
        if exp <= now:
            _GT_NEG_CACHE.pop((net, key), None)
            return False
        return True


def _gt_neg_put(net, key, now=None):
    """「这条链上没有这个地址的池子」→ 记负缓存 5 分钟（⛔ 只放内存）。
    **只缓存空**：命中绝不缓存（价格要新鲜），429 / 连不上也不缓存（网络恢复要能立刻重试）。"""
    now = time.monotonic() if now is None else now
    with _GT_NEG_LOCK:
        for k in [k for k, exp in _GT_NEG_CACHE.items() if exp <= now]:
            _GT_NEG_CACHE.pop(k, None)
        _GT_NEG_CACHE[(net, key)] = now + GT_NEG_TTL


def _gt_neg_reset():
    """测试用：清空负缓存（只放内存，进程退出即消失）。"""
    with _GT_NEG_LOCK:
        _GT_NEG_CACHE.clear()


def _gt_probe_net(net, addr, deadline=None):
    """探**一条链** → (net, pools, status, why)。在探测线程里跑：这条链的成败绝不连累别的链。
      status="hit"     命中 → pools 是 data 列表
        ="empty"   这条链上没有这个地址的池子（200/404 空，或命中负缓存）→ why=None
        ="limited" 429 限流 → 记下原因，**别的链继续跑**（绝不整源放弃）
        ="failed"  连不上 / 其他 HTTP 错
        ="untried" 熔断中 / 预算不够 → 这条链根本没发出去
    不抛异常（调用方在 future.result() 那儿还有一层兜底）。"""
    key = _gt_addr_key(addr)
    if _gt_neg_hit(net, key):
        return net, None, "empty", None            # 5 分钟内探过是空 → 不再重复打这 7 发
    if deadline is not None and time.monotonic() >= deadline:
        return net, None, "untried", "查询预算到点，这条链没试"
    if _cb_is_open("geckoterminal"):
        return net, None, "untried", "GeckoTerminal 自己在熔断中，这条链没试"
    if not _gt_pool_slot(deadline):
        return net, None, "untried", "查询预算不够等 GeckoTerminal 的限速间隔，这条链没试"
    url = URL_GT_TOKEN_POOLS % (urllib.parse.quote(net),
                                urllib.parse.quote((_str(addr) or "").strip()))
    data, err, code, rl = _http_json(url, throttled=False, deadline=deadline,
                                     opener=_GT_OPENER, source="geckoterminal",
                                     budget=ADDRESS_LOOKUP_BUDGET)
    pools = [p for p in _list(_dict(data).get("data")) if isinstance(p, dict)]
    if pools:
        return net, pools, "hit", None             # 命中：**不写负缓存**（价格要新鲜）
    if rl or code == 429:
        return net, None, "limited", "GeckoTerminal 限流(429)，约 1 分钟后恢复"
    if code is None:
        if err and "熔断" in err:                  # 半开探路名额被别人抢了 → 算「没试」
            return net, None, "untried", err
        return net, None, "failed", (err or "连不上")
    if code not in (200, 404):
        return net, None, "failed", "HTTP %s" % code
    _gt_neg_put(net, key)                          # 200/404 且没有 pool = 这条链没有 → 负缓存
    return net, None, "empty", None


def _gt_address_rows(addr, deadline=None):
    """地址 → GeckoTerminal 结果行（**地址查询的主源**）。返回 (rows, note)。

    **并行**探 7 条链（base58 只探 solana；0x 探
    eth/base/bsc/arbitrum/polygon/optimism/gnosis）：
      · ThreadPoolExecutor(max_workers=GT_PROBE_WORKERS=4)：**别开满 7**，免得自己把 GT 打进 429；
        真机单链 1.3~1.5s → 两批 ≈3s（总耗时目标 ≤5s）
      · 某链 429 / 连不上 **绝不让整个源失败**：记下原因，继续等别的链
      · 命中多条链 → **全部列出**（每链取流动性最大的池子 = 一行，按 reserve_in_usd 从大到小），
        每行带自己的链名（chain_id / chain_name / chains 徽章）
      · 负缓存：某链「200/404 但没有池子」→ 5 分钟内不再重复打这条链；命中不缓存
      · 全链都空 → 0 条 + note 写明**试过哪几条链、各链是空还是错**
      · 预算（ADDRESS_LOOKUP_BUDGET 15s）到点 → note 写明**已试 / 未试的链**"""
    addr_s = (_str(addr) or "").strip()
    if not _gt_call_ok():        # 改动③：本次查询的 GT 额度已用完 → 这一档**整体不发**（停手）
        return [], "GeckoTerminal 逐链兜底这一档没发（%s）" % _gt_capped_text()
    is_evm = bool(_MODE_EVM_RE.match(addr_s))
    nets = _gt_nets_for(addr)
    probes = {}                                   # net -> (pools, status, why)

    def _collect(fut, futs):
        net = futs.get(fut)
        try:
            _net, pools, status, why = fut.result()
            probes[_net] = (pools, status, why)
        except Exception as exc:                  # 探测线程自己炸了也不能连累别的链
            probes[net] = (None, "failed", "探测线程异常：%s" % exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=GT_PROBE_WORKERS) as pool:
        futs = dict((pool.submit(_gt_probe_net, net, addr_s, deadline), net) for net in nets)
        pending = set(futs)
        while pending:
            wait_for = None
            if deadline is not None:
                wait_for = deadline - time.monotonic()
                if wait_for <= 0:
                    break
            done, pending = concurrent.futures.wait(
                pending, timeout=wait_for, return_when=concurrent.futures.FIRST_COMPLETED)
            for fut in done:
                _collect(fut, futs)
            if not done:                          # 预算到点还没人答话 → 收手
                break
        for fut in pending:                       # 没探完的：能取消的取消，剩下的如实记下来
            fut.cancel()
            net = futs.get(fut)
            if net is not None and net not in probes:
                probes[net] = (None, "untried", "查询预算到点，这条链没探完")

    state, hits = {}, []                          # net -> (status, why)；命中 [(net, pools)]
    for net in nets:
        pools, status, why = probes.get(net, (None, "untried", "查询预算到点，这条链没探完"))
        state[net] = (status, why)
        if status == "hit":
            hits.append((net, pools))

    def _liq_key(row):
        liq = _num(row.get("liquidity_usd"))
        return liq if liq is not None else -1.0

    rows = []
    for net, pools in hits:
        chain_rows = [r for r in (_gt_row_from_pool(p, addr_s, net) for p in pools) if r is not None]
        if not chain_rows:
            state[net] = ("failed", "命中 %d 个池子但字段一个都读不出来（不猜）" % len(pools))
            continue
        chain_rows.sort(key=_liq_key, reverse=True)
        rows.append(chain_rows[0])                # 每链一行：这条链上取流动性最大的池子
    if rows:                                      # 命中多条链 → 按流动性从大到小**全部列出**
        rows.sort(key=_liq_key, reverse=True)

    tried = [n for n in nets if state[n][0] != "untried"]
    limited = [n for n in nets if state[n][0] == "limited"]
    failed = [n for n in nets if state[n][0] == "failed"]
    untried = [n for n in nets if state[n][0] == "untried"]

    def _detail():
        """各链一句话：是空、是 429、是错、还是没试（预算到点要写明已试/未试）。"""
        parts = []
        for net in nets:
            status, why = state[net]
            if status == "empty":
                parts.append("%s 空" % net)
            elif status == "limited":
                parts.append("%s 被限流(429)" % net)
            elif status == "failed":
                parts.append("%s %s" % (net, why or "失败"))
            else:
                parts.append("%s 没试（%s）" % (net, why or "预算不够"))
        return "；".join(parts)

    if rows:
        problems = []
        if limited:
            problems.append("%d 条链被限流未探（%s）" % (len(limited), "/".join(limited)))
        if failed:
            problems.append("%d 条链探测失败（%s）" % (len(failed), "/".join(failed)))
        if untried:
            problems.append("%d 条链没试（%s）" % (len(untried), "/".join(untried)))
        if not problems:
            return rows, None
        # 照常出结果；但 missing 里要说清「还有链没探到」，别让用户以为只有这一条链
        return rows, ("GeckoTerminal 多链命中 %d 条（%s），但这次探测部分失败：%s"
                      " → 可能还有链上的池子没列出来"
                      % (len(rows), "/".join(_str(r.get("chain_id")) for r in rows),
                         "；".join(problems)))

    if not tried:
        return [], ("GeckoTerminal 全链都没试（%s）" % _detail())
    if limited or failed:
        return [], ("GeckoTerminal 试过 %s，探测失败（%s）"
                    % ("/".join(tried), _detail()))
    return [], ("GeckoTerminal 逐链试过 %s（%s 地址）：没有这个地址的池子（%s）"
                % ("/".join(tried), "EVM" if is_evm else "base58", _detail()))


def _addr_same(a, b, is_sol):
    """地址比较：EVM 小写归一；Solana base58 大小写敏感。"""
    a, b = (_str(a) or "").strip(), (_str(b) or "").strip()
    if not a or not b:
        return False
    return a == b if is_sol else a.lower() == b.lower()


def _ds_pair_token(pair, addr, is_sol):
    """pair 里代表被查地址的那个 token → (token, role)。
    先认 baseToken（任务口径：symbol/name 取 baseToken）；地址只在 quoteToken 上时用 quoteToken
    ——那也是**这个地址**的真实记录（不是猜别的币）。都不是 → (None, None)。"""
    base, quote = _dict(pair.get("baseToken")), _dict(pair.get("quoteToken"))
    if _addr_same(base.get("address"), addr, is_sol):
        return base, "base"
    if _addr_same(quote.get("address"), addr, is_sol):
        return quote, "quote"
    return None, None


# 改动④（真机）：DexScreener 眼里池子地址就是一个 **交易对 / pair**：该行的 symbol 是**对子 base**（`QQQB`）、
# name 是**对子名**（`QQQB / 牛来`）。`_is_lp_token_name` 认不出这种“A / B”（它只看后缀）
# → `_is_pair_like_name` ここ補一层：命中就**不许**把 symbol/name 当接口（丢行、继续看其它源）。
def _ds_pair_is_pool_row(row):
    """DexScreener 行是不是“交易对行”（symbol/name 命中对子名 / 池名）。"""
    r = _dict(row)
    return _is_pair_like_name(r.get("symbol"), r.get("name"))


def _ds_address_pairs(addr, deadline=None):
    """DexScreener /latest/dex/tokens/<addr> → (pairs, note, rate_limited)。不限链。
    拿不到就返回 (None, 人话原因, 是否限流)，绝不抛异常。"""
    url = URL_DEXSCREEN_TOKEN % urllib.parse.quote(addr)
    data, err, code, rl = _http_json(url, throttled=False, deadline=deadline,
                                     source="dexscreener", budget=ADDRESS_LOOKUP_BUDGET)
    pairs = [p for p in _list(_dict(data).get("pairs")) if isinstance(p, dict)]
    if pairs:
        return pairs, None, False
    if rl or code == 429:
        return None, "DexScreener 限流(429)，约 1 分钟后恢复", True
    if code is None:
        return None, ("DexScreener 连不上（%s）；若是代理问题请换 Clash 节点" % (err or "未知错误")), False
    if err:
        return None, "DexScreener 查询失败（%s）" % err, False
    return None, "DexScreener 无此地址的池子", False


def _ds_row_from_pair(pair, addr, is_sol):
    """一个 DexScreener pair → 结果行。字段**只用真实存在的**；缺的写 null + 一句原因。"""
    token, _role = _ds_pair_token(pair, addr, is_sol)
    if token is None:
        return None
    liq = _dict(pair.get("liquidity"))
    vol = _dict(pair.get("volume"))
    chg = _dict(pair.get("priceChange"))
    chain_id = (_str(pair.get("chainId")) or "").strip() or None
    price_usd = _num(pair.get("priceUsd"))
    mcap = _num(pair.get("marketCap"))
    fdv = _num(pair.get("fdv"))
    liq_usd = _num(liq.get("usd"))
    v24 = _num(vol.get("h24"))
    ch24 = _num(chg.get("h24"))
    created = _int(pair.get("pairCreatedAt"))
    nulls = {}
    if price_usd is None:
        nulls["price_usd"] = "该池没给 priceUsd"
    if mcap is None:
        nulls["mcap_usd"] = "DexScreener 没给 marketCap（不拿 fdv 顶替市值）"
    if fdv is None:
        nulls["fdv"] = "该池没给 fdv"
    if liq_usd is None:
        nulls["liquidity_usd"] = "该池没给 liquidity.usd"
    if v24 is None:
        nulls["volume_24h"] = "该池没给 volume.h24"
    if ch24 is None:
        nulls["price_change_24h"] = "该池没给 priceChange.h24"
    if created is None:
        nulls["pair_created_at"] = "该池没给 pairCreatedAt"
    nulls["market_cap_rank"] = "DexScreener 不提供市值排名"
    nulls["drawdown_from_ath_pct"] = "DexScreener 不提供距ATH（CoinGecko 能补就补）"
    nulls["ret_7d_pct"] = nulls["ret_30d_pct"] = nulls["ret_1y_pct"] = \
        "DexScreener 只给 24h 涨跌，不给 7d/30d/1y"
    if chain_id is None:
        nulls["chain_id"] = "该池没给 chainId"
        nulls["chain"] = "该池没给 chainId（链名认不出，绝不硬编）"
    base = {
        "id": None,          # DexScreener 没有 CoinGecko id；补不到就 null，绝不拿地址冒充
        "symbol": _sym(token.get("symbol")),
        "name": _str(token.get("name")),
        "market_cap_rank": None,
        "price_usd": price_usd,
        "drawdown_from_ath_pct": None,
        "ret_7d_pct": None, "ret_30d_pct": None, "ret_1y_pct": None,
        "mcap_usd": mcap,
        "vol24h_usd": v24,
        "chain_id": chain_id,
        "chain_name": _ds_chain_name(chain_id),
        "chain": chain_id,   # 结果行**必须**带链名（下游按「链 + 合约地址」取日线要用）
        "dex_id": _str(pair.get("dexId")),
        "address": (_str(addr) or "").strip() or None,
        "pair_address": _str(pair.get("pairAddress")),
        "liquidity_usd": liq_usd,
        "fdv": fdv,
        "volume_24h": v24,
        "price_change_24h": ch24,
        "pair_created_at": created,
        "null_fields": nulls,
    }
    row = _result_row(base, None, source=DS_TOKEN_SOURCE)
    # 链名也塞进 chains：前端那张卡「链 」那一行就能显示「BSC 币安智能链」
    row["chains"] = [base["chain_name"]] if base["chain_name"] else None
    return row


def _ds_address_rows(addr, deadline=None):
    """地址 → DexScreener 结果行（**不限链，一个链一行**）。返回 (rows, note)。"""
    is_sol = bool(_MODE_SOL_RE.match((_str(addr) or "").strip()))
    pairs, note, _rl = _ds_address_pairs(addr, deadline)
    if pairs is None:
        return [], note, []
    by_chain = {}                      # chainId -> (流动性分, pair)
    for pair in pairs:
        token, _role = _ds_pair_token(pair, addr, is_sol)
        if token is None:
            continue
        chain_id = (_str(pair.get("chainId")) or "").strip().lower() or None
        liq = _num(_dict(pair.get("liquidity")).get("usd"))
        score = liq if liq is not None else -1.0
        cur = by_chain.get(chain_id)
        if cur is None or score > cur[0]:
            by_chain[chain_id] = (score, pair)
    if not by_chain:
        return [], ("DexScreener 有 %d 个交易对，但没有一个是这个地址的 base/quote（不猜）"
                    % len(pairs)), []
    rows, pair_dropped = [], []
    for _score, pair in by_chain.values():
        row = _ds_row_from_pair(pair, addr, is_sol)
        if row is None:
            continue
        if _ds_pair_is_pool_row(row):
            # 改动④：对子名 / 池名不是 symbol → 这行不当最终结果（记 why、继续看其它源）
            pair_dropped.append(row)
            continue
        rows.append(row)
    rows.sort(key=lambda r: (_num(r.get("liquidity_usd")) if _num(r.get("liquidity_usd")) is not None
                             else -1.0), reverse=True)
    for row in rows:
        # 改动②：DexScreener 的 baseToken.symbol 登记进本次查询的退路表（GT 限流时行里的 symbol
        # 就退到它；LP 代币名 / 对子名（池名）照旧被拦下，登记都不登记）
        _sym_fb_put("dexscreener", _dict(row).get("symbol"), _dict(row).get("name"))
    return rows, None, pair_dropped


def _address_enrich_gecko(rows, missing, deadline=None):
    """结果行（GeckoTerminal 或 DexScreener 来的）再拿 CoinGecko **补** id / 市值 / 距ATH / 7d/30d/1y。
    只补**流动性最大的第 1 条**（少打请求，防 429）；补不到就写清原因，保留原有字段。
    CoinGecko 熔断中 → 直接跳过（地址查询不该被 CoinGecko 的熔断挡住：主源不是它）。"""
    if not rows:
        return
    row = rows[0]
    chain_id = (_str(row.get("chain_id")) or "").strip().lower()
    platform = _gecko_platform_for(chain_id)
    if not platform:
        missing.append("CoinGecko 补市值/距ATH 失败（chainId=%s 不在映射表里，不硬编），"
                       "相关字段记 null" % (row.get("chain_id") or "缺失"))
        return
    if _cb_is_open("coingecko"):
        missing.append("CoinGecko 补市值/距ATH 失败（熔断中，本次没发请求；"
                       "DexScreener 的数据照给）")
        return
    # 有 token_address 就优先用它（改动①：用户给池子地址时 address 还是池地址，
    # 拿池地址去 CoinGecko 查合约必然 404 —— 要补的字段是**那个代币**的）
    # ⚠️ 改动③：行里明说「代币没定（gt_blocked）」时**绝不**退回 row["address"] —— 那时 address
    #    是**池子地址**，拿它上游只是白烧一发 + 误报 404。
    addr = _str(row.get("token_address"))
    if not addr and not _dict(row).get("gt_blocked"):
        addr = _str(row.get("address"))
    if not addr:
        return
    url = URL_GECKO_CONTRACT % (platform, urllib.parse.quote(addr))
    data, err, code, rl = _http_json(url, deadline=deadline, source="coingecko")
    if isinstance(data, dict) and _str(data.get("id")):
        cid = _str(data.get("id")).strip()
        cbase = _base_from_coin(data)
        _market_put({cid: cbase})          # 同一币下次按名字 / 地址查 → 零请求
        nulls = row.get("null_fields") if isinstance(row.get("null_fields"), dict) else {}
        for key in ("id", "market_cap_rank", "mcap_usd", "drawdown_from_ath_pct",
                    "ret_7d_pct", "ret_30d_pct", "ret_1y_pct"):
            if cbase.get(key) is not None:
                row[key] = cbase[key]
                nulls.pop(key, None)
        row["screen_score"] = _cs_score(row)
        return
    if code == 404:
        why = "CoinGecko 未收录这个合约（HTTP 404）"
    elif rl or code == 429:
        why = "CoinGecko 限流(429)，约 1 分钟后恢复"
    elif code is None:
        why = "CoinGecko 连不上（%s）" % (err or "未知错误")
    else:
        why = err or "无数据"
    missing.append("CoinGecko 补市值/距ATH 失败（%s），这些字段记 null 且不猜" % why)


def _gecko_address_rows(addr, tvl_idx, missing, deadline=None):
    """（DexScreener 没池子时的兜底）按 CHAIN_PLATFORMS 顺序试 CoinGecko 合约接口，命中即停。"""
    errors = []
    for platform in CHAIN_PLATFORMS:
        url = URL_GECKO_CONTRACT % (platform, urllib.parse.quote(addr))
        data, err, code, _rl = _http_json(url, deadline=deadline, source="coingecko")
        if isinstance(data, dict) and _str(data.get("id")):
            cid = _str(data.get("id")).strip()
            base = _base_from_coin(data)
            _market_put({cid: base})          # 同一币下次按名字 / 地址查 → 零请求
            return [_result_row(base, tvl_idx.get(cid))]
        if code == 429:
            errors.append("%s: CoinGecko 限流(429)，约 1 分钟后恢复" % platform)
        elif code != 404:                     # 404 就是「这条链上没收录」，不算失败
            errors.append("%s: %s" % (platform, err or "无数据"))
    missing.append("合约地址未在 CoinGecko 收录")
    if errors:
        missing.append("CoinGecko 合约查询失败（%s）" % "；".join(errors[:3]))
    return []


# ---- 空结果的 missing 文案：每条都要能看出「**哪个源** + **什么错**」----
# 2026-09-15 真机：地址查询 0 条时 missing 里只有「DexScreener 无此地址的池子」这类**不带错误类型**
# 的句子，用户根本看不出到底是网断了、被熔断了、还是上游真没数据 → 一眼分辨不出「是谁挂了」。
# 错误类型只有这几档（够用且不编）：超时 / 熔断 / 限流 / 连不上 / 预算用完 / 无数据。
_ERR_KINDS = (("超时", ("超时", "timeout", "timed out", "ssl handshake")),
              ("熔断", ("熔断", "封禁", "banned", "rate_limit_banned")),
              ("限流", ("429", "限流", "节流", "too many requests")),
              ("连不上", ("连不上", "连接", "reset", "eof", "不可用", "代理", "tunnel")),
              ("预算用完", ("预算",)),
              ("无数据", ()))


def _err_kind(msg):
    """一句人话原因 → 错误类型（超时 / 熔断 / 限流 / 连不上 / 预算用完 / 无数据）。
    认不出就当「无数据」（上游答了话但没有这个地址的池子）—— 绝不硬套一个更严重的类型。"""
    s = (_str(msg) or "").lower()
    for kind, marks in _ERR_KINDS:
        if any(m in s for m in marks):
            return kind
    return "无数据"


def _src_note(label, note):
    """一条源原因 → 「源名（错误类型）：原因」。
    只在 note 以「源名：」开头时才去掉那半截（别写成「GMGN search（熔断）：GMGN search：…」这种
    车轱辘话）；「DexScreener 无此地址的池子」这类原句**原样保留**（信息一句都不能丢）。"""
    body = (_str(note) or "").strip() or "没给原因"
    for sep in ("：", ":"):
        head = "%s%s" % (label or "", sep)
        if label and body.startswith(head):
            body = body[len(head):].strip() or "没给原因"
            break
    return "%s（%s）：%s" % (label, _err_kind(body), body or "没给原因")


# 已经打过「源名（错误类型）」标签的句子（_src_note 的产物）：识别出来就别重复打
_KIND_TAG_RE = re.compile(r"（(超时|熔断|限流|连不上|预算用完|无数据)）")
# missing 里可能出现在句首的源名（**长名字排前面**，免得「GeckoTerminal」抢了「GeckoTerminal /search/pools」）
_MISSING_SOURCE_NAMES = ("GMGN search", "GeckoTerminal /search/pools", "GeckoTerminal",
                         "DexScreener", "CoinGecko", "CoinPaprika", "DefiLlama",
                         "本地符号索引")


def _tag_missing_lines(missing):
    """空结果时把 missing 归一成「源名（错误类型）：原因」（已经打过标签的原样保留）。
    ⚠️ 只在 0 条结果时用：有结果时 missing 是「部分缺字段」的说明，不需要这层标签。"""
    out = []
    for m in _list(missing):
        line = (_str(m) or "").strip()
        label = None
        for name in _MISSING_SOURCE_NAMES:
            if line.startswith(name):
                label = name
                break
        out.append(line if (label is None or _KIND_TAG_RE.search(line))
                   else _src_note(label, line))
    return out


def _gmgn_addr_rows(addr, deadline=None):
    """地址（0x / base58）→ **GMGN market search**（地址查询第一顺位）→ (rows, note)。

    地址必须与查询**完全一致**才认（复用 _cb_gmgn_search_addr 里那条保护：链对不上 / 地址不符
    → 当没命中，绝不「搜到什么用什么」，也不拿第 2 条）；链只认本工具支持的链。
    ⛔ 复用 gmgn_request 这**唯一**出口（节流 / 缓存 / 熔断全部沿用）→ 不新增未受管的请求路径。
    命中 → 一条 kind=token 的结果行（source="gmgn search"、token_address=查询地址）；
    row["why"] 写清流动性 / 市值 / 是否蜜罐（前端原样显示，绝不编数字）。
    ⚠️ 沙箱无网：真实 GMGN market search 的时延需 host 真机验证（单测全打桩，绝不真调）。"""
    if COSTBASIS is None:
        return [], "costbasis 模块不可用（GMGN 客户端在那边）"
    a = (_str(addr) or "").strip()
    if not a:
        return [], "地址为空（没发请求）"
    budget = GMGN_ADDR_LOOKUP_BUDGET
    if deadline is not None:
        left = deadline - time.monotonic()
        if left <= 0:
            return [], "超出本次查询预算，没发请求（GMGN 虽是第一顺位，预算到点也不硬撞）"
        budget = min(budget, left)
    # Solana base58 大小写敏感：明确按 solana 过滤反查（0x 地址跨链反查）
    hint = "solana" if _MODE_SOL_RE.match(a) else None
    hit, err = _cb_within_budget(_cb_gmgn_search_addr, budget, a, hint)
    if hit is None:
        return [], err or "没命中"
    why = ("GMGN market search 命中 %d 条，其中**地址与查询完全一致**的这条：%s（%s，池子流动性 $%s，"
           "市值 $%s）%s"
           % (hit["n"], hit["symbol"] or hit["name"] or "?", hit["chain"],
              _fmt_liq(hit["liquidity_usd"]), _fmt_liq(hit["mcp"]),
              "（⚠️ 这条本身是蜜罐，请自行核对！）" if hit["honeypot"] else "，这条不是蜜罐"))
    if hit.get("skipped"):
        why += "；跳过 %d 条地址/链对不上的命中（%s）" % (len(hit["skipped"]),
                                                     "、".join(hit["skipped"][:3]))
    pct = "GMGN search 只给现价 / 流动性 / 市值，不给 7d/30d/1y"
    nulls = {"id": "GMGN search 没有 CoinGecko id（不拿地址冒充）",
             "market_cap_rank": "GMGN search 不提供市值排名",
             "drawdown_from_ath_pct": "GMGN search 不提供距ATH（CoinGecko 能补就补）",
             "ret_7d_pct": pct, "ret_30d_pct": pct, "ret_1y_pct": pct,
             "vol24h_usd": "GMGN search 不提供 24h 成交额",
             "fdv": "GMGN search 不提供 fdv（不拿 mcp 顶替）",
             "pair_address": "GMGN search 只回代币，不给池子地址",
             "volume_24h": "GMGN search 不提供 24h 成交量",
             "price_change_24h": "GMGN search 不提供 24h 涨跌",
             "pair_created_at": "GMGN search 不提供建池时间"}
    price_usd = _num(hit.get("price_usd"))
    if price_usd is None:
        nulls["price_usd"] = "GMGN search 没给 price"
    if hit["liquidity_usd"] is None:
        nulls["liquidity_usd"] = "GMGN search 没给 liquidity（流动性）"
    if hit["mcp"] is None:
        nulls["mcap_usd"] = "GMGN search 没给 mcp（市值）"
    base = {"id": None, "symbol": hit["symbol"], "name": hit["name"] or hit["symbol"],
            "market_cap_rank": None, "price_usd": price_usd,
            "drawdown_from_ath_pct": None,
            "ret_7d_pct": None, "ret_30d_pct": None, "ret_1y_pct": None,
            "mcap_usd": hit["mcp"], "vol24h_usd": None,
            "chain_id": hit["chain"], "chain_name": _ds_chain_name(hit["chain"]),
            "chain": hit["chain"],
            "dex_id": None, "address": hit["address"], "pair_address": None,
            "liquidity_usd": hit["liquidity_usd"], "fdv": None,
            "volume_24h": None, "price_change_24h": None, "pair_created_at": None,
            "kind": "token", "token_address": hit["address"],
            "null_fields": nulls}
    row = _result_row(base, None, source=GMGN_SEARCH_SOURCE)
    # 链名也塞进 chains：卡片「链」那一行就能显示「BSC 币安智能链」（和 DexScreener / GT 同一个口径）
    row["chains"] = [base["chain_name"]] if base["chain_name"] else None
    row["why"] = why                                # 命中理由（含流动性/市值/蜜罐）
    # 改动②：GMGN search 报出的 symbol 也登记进退路表（LP 代币名绝不登记）
    _sym_fb_put("gmgn_search", base.get("symbol"), base.get("name"))
    return [row], None


# 改动①里「可能把池子地址当成 LP 代币」的那几档 —— 只有这几档需要过 LP 黑名单：
# GMGN / DexScreener 眼里**池子地址就是一个 LP 的 ERC20**（真机：CAKE-LP）；GT /search/pools
# 那档平时走「池子 vs 代币」判定 + 两侧自证（_gt_pool_token_row），只有判定没命中、symbol 又是
# 池名 base 侧时才需要兜这一层；CoinGecko 是人工收录的币表（照旧，不过滤）。
_ADDR_LP_FILTER_SOURCES = ("gmgn_search", "dexscreener", "geckoterminal_search")


def _drop_lp_rows(rows, src=None):
    """LP 代币黑名单（改动①）：这一行是不是「LP 代币」→ 是就**不作为最终代币**。

    真机事故：同一个池子地址连着查两次，GMGN 那次把池子地址解析成了 LP 代币 CAKE-LP →
    拿 LP 代币去要池子必然 404。命中黑名单的行直接丢掉并继续问下一个源（**绝不当最终结果**）。
    → (kept, note, dropped)：note 是「丢了什么、为什么」的一句人话（没丢 → None），照旧进 missing；
      dropped 是丢掉的原始行 —— 改动①降级路径要用它（身份判定限流时**保留一条降级结果**，
      绝不因为 LP 黑名单把整条查询变成 0 条；见 `_lp_degraded_row`）。
    ⚠️ 只对 `_ADDR_LP_FILTER_SOURCES` 那几档生效（见上面的注释）；CoinGecko 不在这里过滤。"""
    if src not in _ADDR_LP_FILTER_SOURCES:
        return rows, None, []
    kept, dropped = [], []
    for r in _list(rows):
        row = _dict(r)
        if (_is_lp_token_name(row.get("symbol"), row.get("name"))
                or _is_pair_like_name(row.get("symbol"), row.get("name"))):
            # 改动④：对子名 / 池名（如「QQQB / 牛来」→「QQQB」）也不许当最终代币
            dropped.append(row)
        else:
            kept.append(r)
    if not dropped:
        return kept, None, []
    who = "、".join(
        "%s（%s）" % (_str(_dict(r).get("token_address")) or _str(_dict(r).get("address"))
                      or "地址拿不到",
                     _str(_dict(r).get("symbol")) or _str(_dict(r).get("name")) or "symbol 拿不到")
        for r in dropped[:3])
    return (kept,
            ("%s 命中的 %s 是 LP 代币（黑名单）→ **不作为最终代币**，继续问下一个源"
             % (ADDRESS_SOURCE_LABEL.get(src, src), who)),
            dropped)


def _pool_net_of_row(row):
    """结果行里的链（chain_id / chain）→ GeckoTerminal 的 network id。认不出 → None（绝不硬编）。"""
    chain = _str(_dict(row).get("chain_id")) or _str(_dict(row).get("chain"))
    if not chain:
        return None
    c = chain.strip().lower()
    return _str(GT_NETWORKS.get(c)) or (c if c in GT_NET_LABELS else None)


def _pool_restore_from_source_row(row, deadline=None):
    """改动①：某个源明确说「这是 LP / 池子」（symbol / name 命中 LP 黑名单）→
    **切换到池子 → 代币的还原逻辑**（relationships + 自证 + LP 黑名单，见 `_gt_pool_token_row`）。

    身份判定那一枪刚被限流时，这一枪**只打一发**：deadline 压到 1s（`_gt_json` 的 429 退避
    一看预算不够就立刻收手）→ 省额度、也不让用户干等。
    → (结果行, None) 或 (None, 「为什么没还原出来」)。"""
    r = _dict(row)
    own = _str(r.get("address")) or _str(r.get("pair_address"))
    net = _pool_net_of_row(r)
    if not own or not net:
        return None, "这条命中没有「链 + 池子地址」→ 没法用 GeckoTerminal 还原基础代币（不猜）"
    shot = min(deadline, time.monotonic() + 1.0) if deadline is not None \
        else time.monotonic() + 1.0
    pool, why = _gt_pool_by_address(net, own, shot)
    if pool is None:
        return None, "GeckoTerminal 没能给出池子 %s 的两侧代币（%s）" % (own, why or "无数据")
    rr = _gt_search_row_from_pool(pool, own, True, "pool_address",
                                  _gt_rel_token_addr(pool, "base_token"))
    if rr is None:
        return None, "池子 %s 的 attributes 读不出来（不猜）" % own
    rr, _note = _gt_pool_token_row(rr, pool, own, shot)
    if _str(rr.get("token_address")):
        return rr, None
    return None, "GeckoTerminal 也没能把这个池子的两侧代币还原成可用代币"


def _lp_degraded_row(row, src, why):
    """改动①降级结果：身份判定没做成（GT 限流 / 连不上），而某个源明确说「这就是个 LP 代币」。

    LP 代币照旧**绝不当最终代币**（token_address / symbol 都记 null + 原因），
    但**保留这一行**（`results` ≥ 1）+ 把降级原因写进 notes / null_fields / gt_blocked
    —— 「绝不允许因为身份判定限流而返回 results=[]」，也绝不静默。"""
    r = dict(_dict(row))
    sym = _str(r.get("symbol")) or _str(r.get("name")) or "符号拿不到"
    addr = _str(r.get("address")) or _str(r.get("token_address")) or "该地址"
    msg = ("%s 把 %s 解析成 LP 代币「%s」→ 这是**池子 / LP**，不是可分析的基础代币；"
           "GeckoTerminal 限流中，本次没能还原出基础代币（%s）→ 先如实展示这一条"
           % (ADDRESS_SOURCE_LABEL.get(src, src) or src, addr, sym, why or "没给原因"))
    r["kind"] = "lp_pool"
    r["token_address"] = None
    r["symbol"] = None
    r["name"] = None
    r["gt_blocked"] = msg            # 改动③：代币没定 → 不拿池子地址 / LP 代币去要日线
    r["notes"] = [msg]
    nulls = r.get("null_fields") if isinstance(r.get("null_fields"), dict) else {}
    nulls.pop("kind", None)
    nulls["token_address"] = msg
    nulls["symbol"] = "命中的是 LP 代币（黑名单）→ 绝不拿它当 symbol"
    r["null_fields"] = nulls
    return r


def _gt_pool_precheck(addr, deadline=None):
    """地址查询的**池子身份前置判定**（改动①）：先问一次 GT /search/pools「这个地址是不是池子」。

    → (is_pool, rows, notes, pools, note, pre)
      · is_pool=True  → 用户给的是**池子**：rows 就是最终结果（一行 kind=token / lp_pool），
                        notes 是必须带进 missing 的口径说明；**不再看别的源**
                        （别的源会把池子地址当 LP 代币 —— 真机那次的 CAKE-LP 就是这么来的）
      · is_pool=False → rows/notes 空；pools / note 原样带回给 `_gt_search_rows` **复用**
                        （同一件事绝不发两次请求）；pre 里的 status 区分两种「否」：
          - POOL_PRECHECK_NOT_POOL（GT 给了确定回答：不是池子）→ 调用方照旧走多源顺序
          - POOL_PRECHECK_UNKNOWN（GT 限流 / 连不上 = **没判定出来**）→ 调用方**降级继续**：
            按「代币地址」走多源顺序（改动①：**绝不允许**因为身份判定限流就返回 results=[]），
            并把 pool_precheck="unknown_due_to_rate" + notes + blocked_by 如实标在结果里。
    只发 1 个逻辑请求（额度允许的话）；pre = {"status", "kind", "retry_after_s"}。
    ⚠️ 沙箱无网：真实 /search/pools 的连通性与额度**需 host 真机验证**（离线用例全部打桩）。"""
    q = (_str(addr) or "").strip()
    if not q:
        return False, [], [], _GT_POOLS_UNFETCHED, None, {"status": POOL_PRECHECK_NOT_POOL}
    is_sol = bool(_MODE_SOL_RE.match(q))
    pools, note, kind, retry_after_s = _gt_search_pools(q, True, deadline)
    if not pools:
        blocked = _str(kind) in GT_SELF_BLOCKED_KINDS        # rate / down = **没判定出来**
        return False, [], [], pools, note, {
            "status": POOL_PRECHECK_UNKNOWN if blocked else POOL_PRECHECK_NOT_POOL,
            "kind": kind, "retry_after_s": _num(retry_after_s)}
    pool = _gt_pool_verdict_pool(pools, q, is_sol)
    if pool is None:
        return False, [], [], pools, None, {"status": POOL_PRECHECK_NOT_POOL, "kind": "ok"}
    row = _gt_search_row_from_pool(pool, q, True, "pool_address",
                                   _gt_rel_token_addr(pool, "base_token"))
    if row is None:
        msg = ("你给的是池子 %s：这条池子读不出 attributes（不猜）→ 本次不给代币结果"
               % (_gt_pool_own_addr(pool) or q))
        return True, [], [msg], pools, None, {"status": POOL_PRECHECK_POOL, "kind": "ok"}
    row, row_note = _gt_pool_token_row(row, pool, q, deadline)
    return (True, [row], ([row_note] if row_note else []), pools, None,
            {"status": POOL_PRECHECK_POOL, "kind": "ok"})


def _mark_precheck_rows(rows, unknown_pool, pre):
    """改动①：身份判定被限流时的**降级标记**（必须能被看见，绝不静默）。

    给每一行打上 `pool_precheck="unknown_due_to_rate"` + 结构化
    `blocked_by=[{source:geckoterminal, kind:rate/down, retry_after_s}]`
    + notes 里那句「未判定…已按代币处理」→ `_rows_blocked_by` / 面板照抄，前端不新增逻辑。
    unknown_pool=False → 原样返回（既有路径一个字都不变）。"""
    if not unknown_pool:
        return rows
    blocked = {"source": "geckoterminal", "kind": _str(_dict(pre).get("kind")) or "rate",
               "retry_after_s": _ra_out(_dict(pre).get("retry_after_s"))}
    for r in _list(rows):
        row = _dict(r)
        row["pool_precheck"] = POOL_PRECHECK_UNKNOWN
        cur = row.get("blocked_by")
        cur = list(cur) if isinstance(cur, list) else []
        if blocked not in cur:
            cur.append(dict(blocked))
        row["blocked_by"] = cur
        notes = _str_list(row.get("notes"))
        if POOL_PRECHECK_RATE_NOTE not in notes:
            notes.append(POOL_PRECHECK_RATE_NOTE)
        row["notes"] = notes
    return rows


def _do_address(addr, tvl_idx, missing, deadline=None):
    """合约地址查询：**改动①的前置判定 + 按可达性排序的多源**：
      ⓪ GeckoTerminal /search/pools **池子身份前置判定**（一次调用）：
        查询地址 == 某个池子**自己**的地址 → 用户给的是池子 → **只**用池子的 relationships
        （base/quote）+ 自证 + LP 黑名单还原成代币（_gt_pool_token_row），**别的源一律不看**
        （GMGN / DexScreener 会把池子地址当成 LP 代币）→ 同一输入连查 N 次结果完全一致。
        不是池子 → 判定为否，pools 结果**传给下面 geckoterminal_search 复用**（同一件事绝不发两次）。
        ⚠️ 改动①（2026-09-16 真机）：GT 这一枪**限流 / 连不上**（拿不到答案）时**降级继续** ——
        按「代币地址」走下面的多源顺序，并在结果行上标 pool_precheck="unknown_due_to_rate" +
        notes「未判定…已按代币处理」+ blocked_by(geckoterminal/rate)；后面若某个源明确回答
        「这是 LP / 池子」→ 再切到**池子 → 代币的还原逻辑**，还还原不出来就**保留一条降级结果**。
        **绝不允许**因为身份判定限流而返回 results=[]。
      gmgn_search（GMGN market search -q <地址>：第一顺位；命中且**地址完全一致**才认，
        独立主机 + 独立熔断桶 —— 代理把其它源全打死时它照样能出结果；命中 LP 代币黑名单的行丢掉）
      → dexscreener（不限链，一发）
      → geckoterminal_search（GeckoTerminal /search/pools：**一次调用定链**，链名藏在
        data[].id 的 "<network>_<地址>" 前缀里；一次 1 个请求，不再 7 发逐链）
      → geckoterminal（老路径兜底：7 条链**并行**逐链探、某链 429/失败不放弃整源）
      → coingecko（老路径，最后）
    前一个源 0 条 / 报错才试下一个；每个源**独立熔断桶**（一个挂了绝不连累下一个）。
    拿到行之后，能补的字段（id/市值/距ATH/7d/30d/1y）再拿 CoinGecko 补一发（补不到就写清原因）。
    一条都没拿到 → missing 里**每条都带「源名（错误类型）」**（超时/熔断/限流/连不上/预算用完/无数据），
    用户要能一眼看出是**谁挂了**；⛔ GMGN 走 gmgn_request 唯一出口（节流/缓存/熔断全沿用），
    不新增任何未受管的请求路径。
    ⚠️ 行里自带的 notes（如「你给的是池子 0x…（链 bsc），已按基础代币 0x…（BNC4）分析」）也
    **原样带进 missing**（不贴「源名（错误类型）」标签：这是口径说明，不是错误）—— 面板上必须看得到。
    改动③：整个过程受 GT_CALLS_PER_QUERY_MAX 约束（本次查询的 GT 逻辑调用额度，默认 3）；
    额度用完 → 停手 + missing 里如实写「本次 GT 调用已达上限，可能没取全」。
    ⚠️ 沙箱无网：真实连通性与额度**需 host 真机验证**（离线用例全部打桩）。"""
    budget, owned = _gt_budget_open()
    _sym_fb_open()               # 改动②：这一次查询的 symbol 退路表（别的源报出的 symbol 进这里）
    try:
        return _do_address_sources(addr, tvl_idx, missing, deadline)
    finally:
        # 改动③：额度用完就**停手**了 → 结果里如实标注「可能没取全」（绝不静默）
        if budget.capped and _gt_capped_text() not in missing:
            missing.append(_gt_capped_text())
        _gt_budget_close(owned)
        _sym_fb_close()          # ⛔ 退路表只活在这一次查询里（绝不跨查询、绝不落盘）


def _evm_pool_rescue(addr, missing, deadline=None):
    """GT 未确认身份时，用链上直读把「被当成代币的池子」还原成基础代币。

    命中（链上直读读出 token0/token1）：返回那一行（已是代币行，symbol 来自链上）；
    没命中 / 探测超时：返回 None，并往 missing 追加如实说明。**绝不编造**。

    这条路径对代币地址要挨个试 4 条 EVM 链（真机白花 13~17s）→ **按症状触发**，
    只有在「源循环给出的那一行拿不到可用 symbol」时才调用（绝不提前抢预算）。"""
    # GT 没确认身份（rate/down）→ 这个地址**可能是个池子**。
    # 链上直读兜底（真机：GT 限流时这里会给出 symbol=None 或报价侧符号，4Stock/None 都错）。
    _probe_started = time.monotonic()
    for _net in ("bsc", "eth", "base", "polygon_pos"):
        if time.monotonic() - _probe_started >= _EVM_POOL_PROBE_TOTAL_SEC:
            # 总上限到了 → **立即停手**：用已拿到的结果，一条都没有就照旧落到降级路径。
            _note_to = ("链上直读探测超时（>%.0f 秒），未判定是否为池子"
                        % _EVM_POOL_PROBE_TOTAL_SEC)
            if _note_to not in missing:
                missing.append(_note_to)
            break
        _ev = _evm_pool_sides(_net, addr)
        _sides = _list(_dict(_ev).get("sides"))
        if not _sides:
            continue                       # 这条链上不是这个地址 / 没读出来 → 换下一条
        # 是个池子 → **直接用链上直读的结论建行**（手里已经有两侧与符号，
        # 不再绕 _gt_pool_token_row：它内部会重做一轮 GT + 又一次链上直读，
        # 真机把 4.98s 的 _evm_pool_sides 拖成 88.15s → 被查询硬上限砍掉）。
        _idx, _rule = _evm_pick_base(_sides, None)
        _pick = _dict(_sides[_idx]) if 0 <= _idx < len(_sides) else {}
        _sym_ev = _sym(_pick.get("symbol"))
        if not _sym_ev:
            # 选中的那一侧没读出 symbol → 视为没命中（落地到降级路径，**绝不拿池名顶替**）
            continue
        _r2 = {
            "kind": "token",
            "token_address": _str(_pick.get("address")),
            "symbol": _sym_ev,
            "name": _str(_pick.get("name")),
            "decimals": _pick.get("decimals"),
            "symbol_source": "evm_rpc",
            "pair_address": addr,
            "chain_id": _net,
            "chain": _net,
        }
        _note2 = ("你给的是池子 %s（链 %s）；链上直读：%s；已按基础代币 %s（%s）分析"
                  "（选取依据：%s）"
                  % (addr, _net, _evm_sides_text(_sides),
                     _str(_pick.get("address")) or "（地址读不出）",
                     _sym_ev, _rule or "链上约定"))
        # 与 _gt_pool_token_row 成功分支同形：notes 留一份，面板上看得见选取依据
        _r2["notes"] = [_note2]
        if _note2 and _note2 not in missing:
            missing.append(_note2)
        _publish_struct_row(_r2)
        _address_enrich_gecko([_r2], missing, deadline)
        return _r2


def _do_address_sources(addr, tvl_idx, missing, deadline=None):
    """`_do_address` 的正题（改动③的 GT 调用额度已经在外面装好）。"""
    # GT 前置判定只是「锦上添花」，只给它一个短上限（默认 5s）：
    # 真机事故——它卡满 15s 把整段预算吃光，导致第一顺位、实测只要 1.2s 的 GMGN 发不出请求
    # （用户原文「GMGN search（预算用完）：超出本次查询预算，没发请求」）。
    _now = time.monotonic()          # 与 t_start 同一套时钟
    _pre_cap = _now + GT_PRECHECK_BUDGET_SEC
    _pre_deadline = _pre_cap if deadline is None else min(float(deadline), _pre_cap)
    is_pool, prows, pnotes, pre_pools, pre_note, pre = _gt_pool_precheck(addr, _pre_deadline)
    # 改动①：身份判定是 **rate / down**（= 没判定出来）→ 降级继续，绝不因此返回 results=[]
    unknown_pool = _str(_dict(pre).get("status")) == POOL_PRECHECK_UNKNOWN
    if unknown_pool and POOL_PRECHECK_RATE_NOTE not in missing:
        missing.append(POOL_PRECHECK_RATE_NOTE)
    if is_pool:
        for line in pnotes:
            if line and line not in missing:
                missing.append(line)
        if prows:
            _publish_struct_row(prows[0])
            _address_enrich_gecko(prows, missing, deadline)
        return prows
    dropped = []
    for src in ADDRESS_SOURCE_ORDER:
        if src == "gmgn_search":
            rows, note = _gmgn_addr_rows(addr, deadline)
        elif src == "dexscreener":
            rows, note, ds_pair_dropped = _ds_address_rows(addr, deadline)
            if ds_pair_dropped and not rows:
                # 改动④：DexScreener 命中的是“交易对 / 池子”行（symbol=对子 base、name=对子名）
                # → 不许当代币：走池子→代币还原（链上直读优先），还不出来也绝不 0 条。
                dropped.extend(ds_pair_dropped)
                continue
        elif src == "geckoterminal_search":
            # 前置判定那一枪的结果原样复用（**同一件事绝不发两次请求**）
            rows, note = _gt_search_rows(addr, deadline, pools=pre_pools, note=pre_note)
        elif src == "geckoterminal":
            rows, note = _gt_address_rows(addr, deadline)
        else:                                  # coingecko：老路径，它自己会把原因写进 missing
            before = len(missing)
            rows, note = _gecko_address_rows(addr, tvl_idx, missing, deadline), None
            if not rows:                       # 它的原因也照样打上「源名 + 错误类型」标签
                missing[before:] = [_src_note("CoinGecko", m) for m in missing[before:]]
        # 改动①：GMGN / DexScreener 可能把**池子地址**解析成 LP 代币（真机：CAKE-LP）→ 丢掉这行
        rows, lp_note, dropped = _drop_lp_rows(rows, src)
        if lp_note:
            missing.append(lp_note)
        if rows:
            if note:        # 命中也可能有话说：某条链 429 / 没探完 → 如实写进 missing
                missing.append(note)
            # 行**自己**的口径注释（改动①：池子地址 → 代币）照样要出现在面板上
            _mark_precheck_rows(rows, unknown_pool, pre)
            for line in _str_list(_dict(rows[0]).get("notes")):
                if line not in missing:
                    missing.append(line)
            # 第 1 条的身份**已经确定** → 立刻交给并行的结构取数任务：下面这步 CoinGecko 补行情
            #（代理抖动时最慢的一步）再慢，也绝不许把结构盈亏比饿死（2026-09-16 真机事故）。
            # 按症状触发：这一行没有可用的 symbol（真机：池子被当成代币，symbol=None / 报价侧）
            # → 用链上直读兜底纠正，而不是等「一行都没有」。
            if unknown_pool and not _sym(_dict(rows[0]).get("symbol")):
                _rescued = _evm_pool_rescue(addr, missing, deadline)
                if _rescued is not None:
                    return [_rescued]
            _publish_struct_row(rows[0])
            _address_enrich_gecko(rows, missing, deadline)
            return rows        # 链上直读也没救回来 → 原样返回（绝不编造）
        if unknown_pool and dropped:
            # 改动①：某个源明确回答「这是 LP / 池子」→ **切换到池子 → 代币的还原逻辑**；
            # 还原不出来也**绝不让整条查询变成 0 条**：保留一条如实标注的降级结果。
            restored, why = _pool_restore_from_source_row(dropped[0], deadline)
            # ↓↓↓ 新增：GT 还原不出来（典型：GT 限流、那条路只给 1 秒）→ 用链上直读兜底。
            # 真机：池子 0x4682c6bf… 在 GT 限流时只会得到 symbol=None；链上直读 4.8s 给出 BNC4。
            if restored is None or not _sym(_dict(restored).get("symbol")):
                _rescued = _evm_pool_rescue(addr, missing, deadline)
                if _rescued is not None:
                    restored = _rescued
            out = restored if restored is not None else _lp_degraded_row(dropped[0], src, why)
            if restored is None:            # ← 只有**真的**没还原出来才记这句（别在救回来的时候也记）
                missing.append("这个地址是池子 / LP 代币，但本次没能还原成基础代币（%s）"
                               "→ 先按已有信息如实展示这一条" % (why or "GeckoTerminal 限流"))
            _mark_precheck_rows([out], True, pre)
            for line in _str_list(out.get("notes")):
                if line not in missing:
                    missing.append(line)
            _publish_struct_row(out)
            return [out]
        if note:
            missing.append(_src_note(ADDRESS_SOURCE_LABEL.get(src, src), note))
    # 源循环跑完、一行都没有 → 才走链上直读兜底（上一轮挪过来的位置）。
    if unknown_pool and not is_pool:
        _rescued = _evm_pool_rescue(addr, missing, deadline)
        if _rescued is not None:
            return [_rescued]
    return []


_LOOKUP_LOCK = threading.Lock()
_LOOKUP_CACHE = {}          # q -> (写入时间, payload, ttl)

# 所有网络失败文案里都带「失败」；正常的「缺失 / 未收录」不带。
_FAIL_MARK = "失败"


# 改动②：失败腿的分类（「这次是上游挂了」还是「这次真没这个东西」）
_LEG_BLOCKED_KINDS = ("限流", "熔断", "连不上", "超时")     # → rate / down：**不许缓存空结果**


def _lookup_legs(missing):
    """本次查询的失败腿 → [{"kind": "rate"/"down"}, …]（改动②判定「空结果要不要缓存」用）。

    口径沿用 `_err_kind`（missing 里每条人话原因本来就带「源名（错误类型）」，见 `_src_note`）；
    rate（限流 / 熔断）与 down（连不上 / 超时）都算「上游这次答不了话」→ 下一次必须重新问。"""
    out = []
    for m in _list(missing):
        kind = _err_kind(m)
        if kind in ("限流", "熔断"):
            out.append({"kind": "rate"})
        elif kind in ("连不上", "超时"):
            out.append({"kind": "down"})
    return out


def _lookup_ttl(rows, missing, legs=None):
    """失败 / 空结果 / 部分失败 → 只缓存短 TTL，让网络恢复后能立刻自愈。
    吃陈旧缓存的行同理（source=cache）：别让面板 10 分钟都盯着同一份「N 分钟前」的数据。

    改动②（2026-09-16 真机：「0 条被缓存了」）：
      · rows == [] → **最多** LOOKUP_EMPTY_TTL(30s)；而且这次失败里只要有**任何一条腿**
        是 rate / down（限流或连不上）→ **返回 0 = 一秒都不缓存**（下一次直接重新发请求）
      · legs 由调用方（build_lookup）用 `_lookup_legs(missing)` 算好传进来；legs=None
        （老调用方 / 单测直接调）→ 沿用老口径（LOOKUP_FAIL_TTL = 20s ≤ 30s，一样「短」）。"""
    if not rows:
        if any(_str(_dict(l).get("kind")) in GT_SELF_BLOCKED_KINDS for l in _list(legs)):
            return 0                      # 上游这次答不了话 → 0 条不是结论，绝不缓存
        return min(LOOKUP_FAIL_TTL, LOOKUP_EMPTY_TTL)
    for m in (missing or []):
        if _FAIL_MARK in m:
            return LOOKUP_FAIL_TTL
    for r in rows:
        if _dict(r).get("source") == "cache":
            return LOOKUP_FAIL_TTL
    for b in _rows_blocked_by(rows):
        # 降级结论（行里带 rate / down）也别占着 10 分钟：GT 一恢复，下一次查询就要能自愈
        if _str(_dict(b).get("kind")) in GT_SELF_BLOCKED_KINDS:
            return LOOKUP_FAIL_TTL
    return LOOKUP_TTL


def _blocked_by_live(entry):
    """一条结构化 blocked_by → 它的**当前状态**（改动③，2026-09-16 真机）。

    · 非 GT 的源原样返回（GMGN 那套按报文解封时间恢复，两套互不影响）
    · GT：统一冷却已经过期 / 已被半开成功清掉 → **返回 None**（面板上不许再挂历史状态）；
      还在冷却里 → 保留，并补上「已探测 N 次仍失败，下次尝试时间 HH:MM:SS」这些诊断字段
    · retry_after_s 一律走 _ra_out：**>0 才给数字**（没有 0、没有负数、拿不到就是 None）"""
    entry = _dict(entry)
    src = _str(entry.get("source")) or "?"
    if not _cb_is_gt(src):
        out = dict(entry)
        out["retry_after_s"] = _ra_out(out.get("retry_after_s"))
        return out
    if not _gt_entry_keep(src):
        return None
    out = dict(entry)
    out["retry_after_s"] = _ra_out(out.get("retry_after_s"))
    for key, val in _gt_entry_extra(src).items():
        out[key] = val
    return out


def _rows_blocked_by(rows):
    """结果行自带的结构化 blocked_by 汇总 → [{source,kind,retry_after_s}]（按 (源, 类型) 去重）。

    改动⑥（2026-09-16 真机）：GT 的**自证**那一枪被 429 挡住时，token 仍然按池子的 base_token 给，
    但「谁在限流 / 上游说多久恢复」必须照实说出来 —— 前端不新增逻辑，只照抄这个字段（没有数字
    就一个数字都不编：retry_after_s 只能是上游给的 Retry-After，拿不到就是 None）。
    改动③（2026-09-16 真机）：这里是**每次现算**的出口 → GT 的限流结论一过期就不许再出现
    （缓存下来的那份行数据也救不了它）；retry_after_s 也在这里统一成「>0 才给数字」。"""
    out, seen = [], set()
    for r in _list(rows):
        for b in _list(_dict(r).get("blocked_by")):
            if not isinstance(b, dict):
                continue
            live = _blocked_by_live(b)
            if live is None:
                continue
            src, kind = _str(live.get("source")) or "?", _str(live.get("kind")) or "rate"
            if (src, kind) in seen:
                continue
            seen.add((src, kind))
            out.append(live)
    return out


def _lookup_reply(cached):
    """/api/lookup 的统一出口。池子标记每次现算：结果缓存 10 分钟，
    但「刚加进观察池」必须立刻反映出来。GMGN 熔断中时带 gmgn_ban_until（解封时刻，ISO）——
    前端据此显示「GMGN 限流中，预计 HH:MM 恢复」；0 条时 missing 里也补同一句人话。
    改动⑥：结果行自带的结构化 blocked_by（如 GT 自证那一枪被 429 挡住）在这里汇总到响应顶层
    `blocked_by=[{source,kind,retry_after_s}]` —— 前端**不新增逻辑**，照旧只照抄 blocked_by 与 notes。"""
    rows = _stamp_pool_flags(cached["results"], _candidate_ids(), _watchlist_ids())
    missing = list(cached["missing"])
    ban_until = _cb_ban_until()
    if not rows:
        # 0 条结果：每条原因都写成「源名（错误类型）」，最后再补一句照着做就行的建议
        # （2026-09-15 真机：只留一句没信息量的「查询失败」，用户不知道该换节点还是换查询方式）
        missing = _tag_missing_lines(missing)
        # GMGN 熔断中：别把一坨 429 报文糊上去 —— 写成人话 + 准确恢复时刻（前端也照这个显示）
        if ban_until and not any("GMGN search（" in m for m in missing):
            missing.append(_cb_ban_note(ban_until))
        if LOOKUP_EMPTY_ADVICE not in missing:
            missing.append(LOOKUP_EMPTY_ADVICE)
    return {
        "q": cached["q"],
        "mode": cached["mode"],
        "ts": cached["ts"],
        "results": rows,
        "missing": missing,
        # 谁在限流（结构化）：来源可以是自证那一枪（geckoterminal/rate）等 —— 前端只照抄，不自己编
        "blocked_by": _rows_blocked_by(rows),
        # GMGN 熔断中时给解封时刻（ISO8601）：前端显示「GMGN 限流中，预计 HH:MM 恢复」；没熔断 → None
        "gmgn_ban_until": ban_until,
        # 面板那行小字：上游:CoinGecko(熔断中 12s) DexScreener(正常) —— 每次现算
        "upstream": _cb_panel(),
    }


def _breaker_payload(raw, mode):
    """① 熔断期的回话内容：**只用内存里的东西**（查询词缓存 / 本地符号索引 + 按币 id 的行情缓存），
    一个 upstream 请求都不发：
      · 这个查询词缓存过（新鲜、过期都算）→ 照常返回那份数据
      · 没有查询词缓存，但本地索引能认出这个币、行情缓存里有它 → 拿行情缓存凑一份结果
      · 都没有 → results=[] + 写清「代理不可用、熔断 25 秒、恢复后自动重试」"""
    with _LOOKUP_LOCK:
        hit = _LOOKUP_CACHE.get(raw)
        cached = hit[1] if hit else None      # ⚠️ 这里**不判 TTL**：过期的也比空手强
    if cached is not None:
        return cached
    rows = []
    if mode == "search":
        # allow_fetch=False：熔断期间连「后台拉索引」都不挂（那也是上游请求）
        picked = order_search_coins(_local_index_match(raw, allow_fetch=False) or [], raw)
        now = time.time()
        for coin in picked:
            cid = (_str(coin.get("id")) or "").strip()
            if not cid:
                continue
            rec, age = _market_get(cid, now)
            if rec is None:
                continue
            base = dict(_dict(rec).get("base"))
            if base.get("market_cap_rank") is None:
                base["market_cap_rank"] = _int(coin.get("market_cap_rank"))
            rows.append(_result_row(base, None, source=_market_origin_source(rec), age_s=age))
    note = _cb_msg()
    if rows:
        rows = _sort_rows_by_rank(rows)
        note = ("CoinGecko 不可用，以下来自本地缓存（%s）；%s"
                % ("、".join(sorted(_str(r.get("id")) or "?" for r in rows)), note))
    # 0 条也是「谁挂了」要一眼看出：熔断这句照样打上「源名 + 错误类型」标签
    return {"q": raw, "mode": mode, "ts": _now(), "results": rows,
            "missing": [_src_note("CoinGecko", note)]}


def build_lookup(q):
    """GET /api/lookup 的载荷；q 为空返回 None（调用方回 400）。
    顺序：① 代理熔断中 → 只用内存秒回 → 查询词缓存 → 走 _do_search / _do_address。
    预算：名称搜索 LOOKUP_BUDGET(12s)、地址查询 ADDRESS_LOOKUP_BUDGET(15s，
    一次定链就够，逐链探测只当兜底且同样受这个预算约束)。

    结构盈亏比（第 1 条）**和行情富化并行**、用**自己的** STRUCT_BUDGET_SEC(20s)：
    2026-09-16 真机事故就是「行情富化把 LOOKUP_BUDGET 吃光 → 结构块连一次请求都没发」。
    现在两份预算各算各的，行情再慢也饿不死结构；整体上限 = max(两份) + 2s 余量。
    ⚠️ 结构取数的 deadline **从交接点（拿到第 1 条身份那一刻）才起算**（见 _struct_prefetch）：
    行情侧慢到 10+ 秒才交接时，结构照样有整整一份自己的预算（老口径从请求开始算 → 一动手就秒超）。"""
    raw = (q or "").strip()
    if not raw:
        return None
    now = time.time()
    mode = detect_mode(raw)
    # 总预算从进函数就开始算。地址查询现在用 GeckoTerminal /search/pools **一次定链** →
    # 单独给 ADDRESS_LOOKUP_BUDGET(15s)（逐链兜底也在这 15 秒里）；名称搜索保持 LOOKUP_BUDGET(12s)。
    budget = ADDRESS_LOOKUP_BUDGET if mode == "address" else LOOKUP_BUDGET
    t_start = time.monotonic()                    # 结构诊断的时间原点（响应里 debug.timings）
    deadline = t_start + budget
    # 结构盈亏比（只算第 1 条）**自己的**预算：deadline 从**交接点**起算（见 _struct_prefetch），
    # 这里只是熔断分支（不取数）用的占位；绝不再和行情富化取 min。
    struct_deadline = t_start + STRUCT_BUDGET_SEC
    # 整体接口上限 = max(两份预算) + 2s 余量（默认 22s）：响应不会比现在慢太多；
    # 交接始终没来（行情侧挂了）时 _struct_prefetch 就等到这个上限兜底，绝不无限等。
    hard_deadline = t_start + LOOKUP_TOTAL_TIMEOUT_SEC

    # ① CoinGecko 熔断中：**立刻回话**（零上游请求、毫秒级），有缓存吃缓存、没缓存就写清原因。
    #    别让每个查询都把整个预算烧光才回 0 条 —— 那正是用户看到的「慢 + 查不到」。
    #    ⚠️ 但**地址查询**例外：它的主源是 GeckoTerminal / DexScreener，CoinGecko 的熔断不该拦它
    #    （这两个源各自的熔断在 _do_address 里独立判定）。
    if mode != "address" and _cb_is_open("coingecko"):
        # 熔断中：结构块只吃内存缓存（有就照给），**一个上游请求都不发**
        payload = _lookup_reply(_breaker_payload(raw, mode))
        return _attach_struct(payload, raw, struct_deadline, allow_fetch=False,
                              timings=_struct_timings(t_start, None, None, None))

    # ② 结构取数**先丢进池子**（同一请求只 1 个并发任务；GMGN 出口自带的节流 / 缓存 / 熔断
    #    和内部锁一个都不绕过）——行情富化一确定第 1 条的身份，就把身份交给它，两边并行。
    #    池子是**每个请求自己一个**：一个慢查询绝不排队挡住别人的结构取数。
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=STRUCT_FETCH_WORKERS,
                                                 thread_name_prefix="struct-daily")
    slot = _StructSlot()
    fut = pool.submit(_struct_prefetch, slot, raw, t_start, hard_deadline)
    prev_sink = getattr(_STRUCT_ROW_SINK, "sink", None)
    try:
        _STRUCT_ROW_SINK.sink = slot.put          # 行情侧 → 结构任务的交接点（thread-local）
        with _LOOKUP_LOCK:
            # 过期条目**不删**（LOOKUP_CACHE_KEEP 才清）：熔断时它就是唯一能回话的东西
            for k in [k for k, v in _LOOKUP_CACHE.items() if now - v[0] >= LOOKUP_CACHE_KEEP]:
                _LOOKUP_CACHE.pop(k, None)
            hit = _LOOKUP_CACHE.get(raw)
            cached = hit[1] if (hit and now - hit[0] < hit[2]) else None

        if cached is None:
            missing = []
            # TVL 富集不许阻塞查询：索引没缓存就跳过（TVL 记 null），后台异步刷新那 ~8.8MB
            tvl_idx, llama_err = _llama_index_ready()
            if tvl_idx is None:
                missing.append("DefiLlama 协议 TVL 缺失（%s）：TVL / 类别 / 链 记 null"
                               % (llama_err or "未知原因"))
                tvl_idx = {}
            rows = (_do_address(raw, tvl_idx, missing, deadline) if mode == "address"
                    else _do_search(raw, tvl_idx, missing, deadline))
            cached = {"q": raw, "mode": mode, "ts": _now(), "results": rows, "missing": missing}
            # 改动②：失败 / 部分失败 → 只缓存 20 秒；0 条最多 30 秒；这次失败里只要有
            # rate / down 的腿 → ttl=0 = **一秒都不缓存**（下一次直接重新发请求，绝不把
            # 「0 条」当成一个可复用的结论 —— 真机那次代理抖一下的 0 条就是这么被缓存住的）。
            ttl = _lookup_ttl(rows, missing, _lookup_legs(missing))
            with _LOOKUP_LOCK:
                if ttl and ttl > 0:
                    _LOOKUP_CACHE[raw] = (time.time(), cached, ttl)
                else:
                    _LOOKUP_CACHE.pop(raw, None)

        payload = _lookup_reply(cached)
        # 行情侧一直没交身份（缓存命中 / 0 条 / 替身 _do_search…）→ 这里补交第 1 条
        if payload.get("results"):
            slot.put(payload["results"][0])
    finally:
        _STRUCT_ROW_SINK.sink = prev_sink
        slot.close()          # 行情侧的动作全做完了 → 放行结构任务（绝不让它干等）
        pool.shutdown(wait=False)
    # 结构盈亏比只挂**第 1 条**（第 2~6 条保持现状）；⛔ 只放内存，一个文件都不落
    return _attach_struct_prefetched(payload, raw, fut, hard_deadline)


# ------------------------------------- 结构盈亏比（只算查询结果的**第 1 条** · ⛔ 只放内存）
# 用户方法论铁律：**这是结构口径，不是买卖建议** —— 指标只说明「当下在哪」，
# 不构成买入信号；分数只触发进一步研究。前端标题里也写死这句话。
#
# 为什么只算第 1 条：一次查询最多 6 条结果，条条都拉一份日线 = 每次查询烧 6 份额度，
# CoinGecko / GeckoTerminal 很快 429 → **只给第 1 条算**，其余结果保持现状。
#
# 日线三条路（按顺序，绝不「瞎补别的源」；每条都有自己的子预算，见 STRUCT_SRC_BUDGET）：
#   ① GMGN market kline --resolution 1d（任意链，含 BSC；熔断/封禁 → 零请求立刻跳下一档）
#   ② Binance 现货日线（主流币，免 key）：klines?symbol=<SYMBOL>USDT&interval=1d&limit=400
#      HTTP 400 = 币安未上市 —— 这不是错误，是「这个币不走币安」→ 直接走 ③
#   ③ GeckoTerminal（meme / 未上币安 / 币安未上市，**两条腿共用一份 GT 子预算**）：
#      ③a 结果行的 chain + token_address → /networks/{chain}/tokens/{addr}/pools?page=1
#          按 reserve_in_usd 降序**依次试前 GT_POOL_TRY_MAX(3) 个池子**：
#          /networks/{chain}/pools/{pool}/ohlcv/day?limit=200
#          （一个池子 404 / 空数据**不代表这个币没日线** → 换下一个；试过的 (池子, 结果) 进
#            meta.attempts；熔断停手、429 按 Retry-After 退避重试；全试完仍无数据才说「没日线」）
#          （**不依赖 GMGN / Binance**；结果行带了链和地址就走这条）
#      ③b 老路径：DexScreener search 拿 pairAddress + chainId → GT 日线
#      ⚠️ GT 日线只回溯 ~180 天（实测 184 根）→ 这条路的高点只能叫 hi_180d，**不是 ATH**
#      ⚠️ GT 限流很紧（~30/min，429 要 10–20 秒才恢复）→ 串行 + 相邻请求 ≥ GT_MIN_INTERVAL
#
# ⛔ 日线 / 结构结果只放内存（_STRUCT_CACHE），绝不落盘（用户明确要求：磁盘上不建库）。
def _sec_label(v):
    """秒数写给人看：12.0 → "12"，8.5 → "8.5"（文案里别出现 12.0s 这种啰嗦写法）。"""
    return "%g" % float(v)


def _fit_src_budgets(raw, parent):
    """结构取数的**子预算适配**：保证 sum(子预算) ≤ 父预算（STRUCT_BUDGET_SEC）。

    2026-09-16 真机事故：老默认 5+4+5=14s > STRUCT_BUDGET_SEC=12s → 排在最后的 GeckoTerminal
    永远分不到时间（「不报错、静默饿死」）。现在子预算之和 > 父预算时**按比例整体缩小**
    （谁都不许被单独饿死），并返回 scaled=True 让 debug 写出实际生效值。"""
    raw = {k: max(0.0, float(v)) for k, v in raw.items()}
    parent = max(0.0, float(parent))
    total = sum(raw.values())
    if total <= parent or total <= 0:
        return {k: min(v, parent) for k, v in raw.items()}, False
    factor = parent / total
    out = {k: min(v * factor, parent) for k, v in raw.items()}
    over = sum(out.values()) - parent          # 浮点尾巴：从最大的一项扣掉，保证 sum ≤ parent
    if over > 0:
        big = max(out, key=lambda kk: out[kk])
        out[big] = max(0.0, out[big] - over)
    return out, True


def _struct_budget_detail():
    """结构取数预算的**自证文案**：说清它是自己的预算、和行情富化各算各的，并列出实际生效的子预算。"""
    subs = " / ".join("%s %ss" % (lab, _sec_label(STRUCT_SRC_BUDGET.get(src, STRUCT_BUDGET_SEC)))
                      for src, lab in (("gmgn", "GMGN"), ("binance", "Binance"),
                                       ("geckoterminal", "GeckoTerminal")))
    extra = "，子预算按比例缩小后生效" if STRUCT_SRC_BUDGET_SCALED else ""
    return ("STRUCT_BUDGET_SEC=%ss（结构取数自己的预算，和行情富化各算各的，可 env 覆盖；"
            "子预算 %s%s）" % (_sec_label(STRUCT_BUDGET_SEC), subs, extra))


def _struct_src_budget_effective():
    """实际生效的子预算（纯数字）；越界时就是自动缩放后的值 —— debug 里照写。"""
    return {k: float(v) for k, v in STRUCT_SRC_BUDGET.items()}


def _struct_timings(t_start, t_handoff, t_begin, t_end, struct_deadline=None):
    """结构取数的**四个时刻** + 关键耗时 → 纯数字 dict（monotonic 秒，诊断用，不参与算法）。

    t_start=请求进 build_lookup；t_handoff=拿到第 1 条身份那刻（结构预算从这里起算）；
    t_struct_begin/t_struct_end=真正开始 / 结束取数。缺的用邻近时刻顶上，绝不让 None 混进来。"""
    t0 = float(t_start if t_start is not None else time.monotonic())
    th = float(t_handoff if t_handoff is not None else t0)
    tb = float(t_begin if t_begin is not None else th)
    te = float(t_end if t_end is not None else tb)
    out = {"t_start": round(t0, 6), "t_handoff": round(th, 6),
           "t_struct_begin": round(tb, 6), "t_struct_end": round(te, 6),
           "handoff_sec": round(th - t0, 6), "fetch_sec": round(te - tb, 6),
           "struct_budget_sec": round(float(STRUCT_BUDGET_SEC), 6)}
    if struct_deadline is not None:
        out["struct_deadline_sec"] = round(float(struct_deadline) - t0, 6)
    return out


def _struct_debug(timings=None, cached=False):
    """结构盈亏比的**诊断块**（给排查用，不参与任何算法）：
      · timings：t_start / t_handoff / t_struct_begin / t_struct_end（**纯数字**）
      · src_budget：实际生效的子预算（自动缩放后的真值）· src_budget_scaled
      · cached：这份是多少秒前算好的（缓存命中时为 True）"""
    t = dict(timings or {})
    return {"timings": {k: v for k, v in t.items() if isinstance(v, (int, float))},
            "src_budget": _struct_src_budget_effective(),
            "src_budget_scaled": bool(STRUCT_SRC_BUDGET_SCALED),
            "cached": bool(cached)}


# 结构取数**自己的**预算（秒）：默认 20s，可用 env STRUCT_BUDGET_SEC 覆盖。
# 2026-09-16 真机事故（0xbeea…7777）：行情富化（DexScreener/GT/CoinGecko）在代理抖动时把
# LOOKUP_BUDGET=12s 吃光 → 结构取数拿到的 deadline 已经过去 → 第 1 条卡片最重要的结构盈亏比
# **一次请求都没发**就写「超出本次查询预算」。现在结构取数和行情富化**并行**、**各算各的预算**，
# 行情再慢也不许把结构饿死（见 build_lookup / _struct_prefetch）；deadline 更是**从交接点起算**
# （拿到第 1 条身份那刻才 now + STRUCT_BUDGET_SEC），老口径「请求开始 + 12s」在交接晚到时会秒超。
STRUCT_BUDGET_SEC = _env_float("STRUCT_BUDGET_SEC", 20.0)
# ---- 每源**独立子预算**（秒）：一个源挂死 / 卡住，绝不许吃光整块结构预算 ----
# 默认 GMGN 5s / Binance 5s / GeckoTerminal 10s（5+5+10=20s = 父预算 20s，三个源都问得到）；
# 一旦 env 把子预算之和顶到 > STRUCT_BUDGET_SEC → _fit_src_budgets **按比例整体缩小**
# （不报错、不静默饿死），实际生效值写进 struct_rr.debug.src_budget。可用 env 覆盖：
#   STRUCT_SRC_BUDGET_GMGN / STRUCT_SRC_BUDGET_BINANCE / STRUCT_SRC_BUDGET_GT
# ⚠️ 2026-09-16 真机事故（0x4682c6bf…，GMGN 熔断中）：主池解析**正确**（$4.56M）但 GT 那一档
#    在「15s 父预算 + 5s 子预算 + 自身节流/429 退避」下**没时间发 OHLCV 请求** →
#    结构块整块失败。GMGN 封的是共享出口 IP（换不了节点）→ **GMGN 被封是常态，GT 必须独立顶住**。
#    所以：父预算提到 20s、GT 拿到 10s（真能跑完列池子 + 主池 OHLCV + 退避），子预算之和 = 父预算。
STRUCT_SRC_BUDGET_REQUESTED = {
    "gmgn": _env_float("STRUCT_SRC_BUDGET_GMGN", 5.0),
    "binance": _env_float("STRUCT_SRC_BUDGET_BINANCE", 5.0),
    "geckoterminal": _env_float("STRUCT_SRC_BUDGET_GT", 10.0),
}
STRUCT_SRC_BUDGET, STRUCT_SRC_BUDGET_SCALED = _fit_src_budgets(STRUCT_SRC_BUDGET_REQUESTED,
                                                               STRUCT_BUDGET_SEC)
# 整体接口超时上限 = max(行情预算, 结构预算) + 这点余量（默认 max(12, 20) → 22s）：
# 响应不会比现在慢太多，同时结构块有整整一份自己的预算可用（前端 Q_TIMEOUT_MS=45s 够用）。
LOOKUP_TOTAL_MARGIN_SEC = 2.0
GT_PRECHECK_BUDGET_SEC = _env_float("GT_PRECHECK_BUDGET_SEC", 5.0)   # GT 前置判定的等待上限（秒）
LOOKUP_TOTAL_TIMEOUT_SEC = max(LOOKUP_BUDGET, STRUCT_BUDGET_SEC) + LOOKUP_TOTAL_MARGIN_SEC
# 结构取数**同一请求内只允许 1 个并发任务**：一个币一份日线就够，多开只会撞 GMGN 配额 /
# GT 30次/分 的限流（GMGN 出口本身有节流+缓存+熔断和内部锁，这里再按住并发数）。
STRUCT_FETCH_WORKERS = 1
STRUCT_TTL = 600           # 算成功 → 缓存 10 分钟（同一币换词再查 = 零请求）
STRUCT_FAIL_TTL = 60       # 429 / 网络这类**暂时**拿不到 → 60 秒后再试
STRUCT_NODATA_TTL = 1800   # 币安未上市 + GeckoTerminal 无池 → 半小时内不重复试（没有就是没有）
STRUCT_CACHE_MAX = 200     # 内存里最多留这么多条（新的优先）
STRUCT_HI_DAYS = 180       # 「近 180 日高点」窗口
STRUCT_LO_DAYS = 90        # 「近 90 日低点」窗口
STRUCT_MA_DAYS = 200       # 200 日简单均线
# 真机事故（2026-09-15，BSC「牛来」0xbeea…7777，8 月 13 上线）：只有 34 根日线时，
# lo_90d 抓到的是**上市首根 K 线的插针价** → 「到前低 −99.99%」是假的 → 结构比被压成 0.42。
# 2026-09-16 真机复核（同一个币，bars_n=34）：只剔首根**还是**粉尘价 —— 上市初期的**前几十根**
# K 线全是接近 0 的池子粉尘价（价格后来涨了几十万倍），lo_ex_first_bar=6.6289256e-06 →
# 仍然是 −99.99% 这种无意义跌幅。所以新币的下行参照位改成**近期窗口低点**：
#   w = max(STRUCT_YOUNG_RECENT_MIN, bars_n // 2)，且 w ≤ bars_n ——「最近 w 根的最低价」。
#   · bars_n=34 → w=17；bars_n=20 → w=14；bars_n=10 → 14 被上限夹回 10（口径确定性，写进输出）
#   · 上市以来最低（lo_since_listing）只当**参考信息**，绝不参与 downside / 结构比
#   · bars_n < STRUCT_YOUNG_MIN_BARS(7) → 参照位直接 None + 原因（绝不硬编一个数）
# 参照位必须跟**币龄**匹配：样本不足就换口径并写出来，宁可说「样本不足」也不给失真比值。
STRUCT_YOUNG_BARS = 60     # 日线少于这么多根 = 样本不足（下行参照位改「近期窗口低点」+ 明说）
STRUCT_YOUNG_MIN_BARS = 7      # 少于这么多根：连近期窗口都撑不起来 → 参照位 null + 原因
STRUCT_YOUNG_RECENT_MIN = 14   # 近期窗口下限：w = min(max(14, bars_n // 2), bars_n)
STRUCT_MIN_MA_BARS = 30    # 少于这么多根 → 均线样本不足，直接 null + 原因（绝不硬算）
STRUCT_ATR_DAYS = 14       # ATR 周期（日线）
STRUCT_VOL_TREND_DAYS = 30 # 「近 30 日 / 前 30 日」量能对比
STRUCT_VOL_TREND_MIN_PCT = 10.0   # 量能变化 ≤10% 当噪声，不进反方理由
STRUCT_MIN_BARS = STRUCT_ATR_DAYS + 2
GT_MIN_INTERVAL = 2.0      # GeckoTerminal ~30 次/分钟 → 相邻请求至少隔 2 秒
URL_BINANCE_KLINES = ("https://api.binance.com/api/v3/klines"
                      "?symbol=%sUSDT&interval=1d&limit=400")
URL_DEXSCREEN_SEARCH = "https://api.dexscreener.com/latest/dex/search?q=%s"
URL_GT_OHLCV = ("https://api.geckoterminal.com/api/v2/networks/%s/pools/%s"
                "ohlcv/day?limit=1000&currency=usd")
# ⚠️ 任务口径：按「链 + 代币地址」这条路取日线用 limit=200（GT 日线只回溯约 180 天，
#    要多了也没有）；**不依赖 GMGN / Binance**，BSC / Base / ETH / SOL 都适用。
URL_GT_TOKEN_OHLCV_DAY = ("https://api.geckoterminal.com/api/v2/networks/%s/pools/%s"
                          "ohlcv/day?limit=200&currency=usd")
# 改动②（2026-09-16 真机）：按「链 + 代币地址」取日线时，**一个池子 404 / 空数据不代表团币没日线**
#   —— 按 reserve_in_usd 降序依次试前 GT_POOL_TRY_MAX 个池子（默认 3，env 可覆盖）。
#   真机证据：0x4682c6bf…（池子）→ 基础代币 0x7c8d5502… 有 20 个池子，最大的那个 liq $4.53M 有日线。
GT_POOL_TRY_MAX = max(1, int(_env_float("GT_POOL_TRY_MAX", 3.0)))
# 「换下一个池子」那几次重试至少留这么多秒给请求（clamp 的下限；见 _gt_slot）
GT_RETRY_MIN_LEFT = 0.25
# ---- 改动②（2026-09-16 真机）：GT 429 要「等得起」，但**主池 OHLCV 那一枪必须优先** ----
# 真机证据（0x4682c6bf…，GMGN 熔断中）：池子找**对了**（liq $4.56M）却 bars_n=0、
#   kind=down / reason「超出查询预算 15.0s」—— 列池子那一步把 GT 子预算全耗在 429 退避上，
#   等轮到 OHLCV 时 deadline 已经过去，**一次日线请求都没发**。
# 现在：① GT 子预算 10s；② 列池子（+它的 429 退避）最多用到「子预算 − GT_OHLCV_RESERVE_SEC」；
#       ③ OHLCV 438/429 按上游 Retry-After 退避重试（没有就 GT_429_BACKOFF_SEC），最多再试 2 次。
GT_429_RETRY_MAX = max(0, int(_env_float("GT_429_RETRY_MAX", 2.0)))
GT_429_BACKOFF_SEC = _env_float("GT_429_BACKOFF_SEC", 2.0)   # 上游没给 Retry-After 时的退避
GT_OHLCV_RESERVE_SEC = _env_float("GT_OHLCV_RESERVE_SEC", 4.0)
# 退避重试用完还是 429 → 就这一句（前端只照抄后端结构化字段，不自己编数字）
GT_429_RATE_TEXT = "GeckoTerminal 限流中（HTTP 429），稍后自动可查"
GT_POOLS_429_RATE_TEXT = "GeckoTerminal 池子列表限流中（HTTP 429），稍后自动可查"
# ---- 改动③（2026-09-16 真机）：GT 额度要省着用（我们自己把它打限流的）----
#   一次地址查询里，GT 的调用**最多发这么多**（默认 3）：超出就停手、用已有结果，
#   并在结果里如实标注「本次 GT 调用已达上限，可能没取全」。
#   计数口径 = **逻辑调用**（一个端点一枪；那一枪内部按 Retry-After 的 429 退避重试
#   —— _gt_json / _http_json 的那层 —— 不重复计数：属于同一枪的退避重试，且已受 GT 子预算约束）。
#   身份判定那一枪（/search/pools）的结果**复用**给「按名字/地址找池子」那一档 → 只算一枪。
#   ⛔ 逐链兜底（_gt_address_rows）：**整档只吃 1 个额度**（额度已用完就一档都不发 = 停手），
#     但档内的 7 发探测**不逐个计数** —— 它是最后一条路，自带 per-chain 节流 / 负缓存 / 查询预算，
#     按「每链一枪」去卡会把「7 条链全探」这个既有能力砍掉（真机口径：一次地址查询最多 3 个额度）。
GT_CALLS_PER_QUERY_MAX = max(1, int(_env_float("GT_CALLS_PER_QUERY_MAX", 3.0)))
GT_CAPPED_TEXT = "本次 GT 调用已达上限，可能没取全"
# ---- 改动①（2026-09-16 真机）：池子身份判定被限流时**降级继续**，绝不把整条查询变成 0 条 ----
POOL_PRECHECK_POOL = "pool"                      # 判定出来了：这个地址就是某个池子自己
POOL_PRECHECK_NOT_POOL = "not_pool"              # GT 给了确定回答：不是池子（或没这个地址）
POOL_PRECHECK_UNKNOWN = "unknown_due_to_rate"    # GT 限流 / 连不上 → **没判定出来**
# 降级继续时写进 missing / 结果行 notes 的那句话（用户必须一眼看出这是「没判定」而不是「没这个币」）
POOL_PRECHECK_RATE_NOTE = ("GeckoTerminal 限流中，未判定这个地址是池子还是代币；已按代币处理")
# DexScreener 的 chainId 和 GeckoTerminal 的 network id 不是一套（ethereum → eth）
GT_NETWORKS = {"ethereum": "eth", "binance-smart-chain": "bsc", "bsc": "bsc",
               "solana": "solana", "base": "base", "arbitrum": "arbitrum",
               "polygon": "polygon_pos", "avalanche": "avax", "optimism": "optimism",
               "tron": "tron", "pulsechain": "pulsechain", "blast": "blast"}
# ---- 结构盈亏比的日线候选源（**全挂才跳过**，见 _attach_struct）----
# 2026-09-15 真机（代理半死）：GMGN 完全健康（kline 也能出日线），但 CoinGecko 熔断 /
#   DexScreener SSL 握手超时 / GeckoTerminal 连不上 → 老代码只看「coingecko 或 dexscreener 熔断」
#   就把整块结构盈亏比跳过，**GMGN 一次都没问到**。现在只有这 4 个源**全都**不可用才停手。
STRUCT_SOURCES = ("gmgn", "binance", "dexscreener", "geckoterminal")
STRUCT_SOURCE_LABELS = ("GMGN", "Binance", "DexScreener", "GeckoTerminal")

_STRUCT_LOCK = threading.Lock()
_STRUCT_CACHE = {}          # key -> {"ts": epoch, "payload": {...}, "ttl": 秒}
_GT_LOCK = threading.Lock()
_GT_LAST = [0.0]


# ---- 改动③：一次查询里 GT 的**调用额度**（GT_CALLS_PER_QUERY_MAX，默认 3）----
# 用 thread-local 装：一次 /api/lookup 的地址解析在**一个**线程里跑（结构取数那条腿在自己的
# 线程里，不受这个额度约束 —— OHLCV 必须发得出去）；单测直接调某个 GT 函数时**没装额度** →
# 永不拦（老用例的行为一点不变）。
_GT_QUERY_TLS = threading.local()


class _GtQueryBudget(object):
    """一次查询的 GT 调用余额：`take()` 放行并计数，超了就 `capped=True` → 停手（用已有结果）。"""

    def __init__(self, limit):
        self.limit = max(1, int(limit))
        self.used = 0
        self.capped = False
        self._lock = threading.Lock()

    def take(self):
        with self._lock:
            if self.used >= self.limit:
                self.capped = True          # 有过「想发但没额度」→ 结果里要如实标注
                return False
            self.used += 1
            return True


def _gt_budget_new(limit=None):
    return _GtQueryBudget(GT_CALLS_PER_QUERY_MAX if limit is None else limit)


def _gt_budget_armed():
    return getattr(_GT_QUERY_TLS, "budget", None)


def _gt_budget_open():
    """装一次查询的额度 → (budget, owned)。已经有（外层装的）就原样复用，别把计数清零。"""
    cur = _gt_budget_armed()
    if cur is not None:
        return cur, False
    b = _gt_budget_new()
    _GT_QUERY_TLS.budget = b
    return b, True


def _gt_budget_close(owned):
    if owned:
        _GT_QUERY_TLS.budget = None


def _gt_call_ok():
    """这一发 GT 逻辑调用还放不放行（放行就计数）。没装额度 → 永远放行。"""
    b = _gt_budget_armed()
    return True if b is None else b.take()


def _gt_capped_text():
    """「已达上限」的那句人话（带上限数字，前端只照抄）。"""
    b = _gt_budget_armed()
    n = b.limit if b is not None else GT_CALLS_PER_QUERY_MAX
    return "本次 GT 调用已达上限（%d 次），可能没取全" % n


def _gt_slot(deadline=None, clamp=False):
    """GeckoTerminal 全局串行：相邻请求至少隔 GT_MIN_INTERVAL 秒（~30/min 的额度很紧）。
    等这次间隔会超过本次预算 → 直接放弃（返回 False），别让用户干等一个注定拿不到的结果。
    clamp=True（**只给「换下一个池子」那几次重试用**，见 _bars_from_geckoterminal_chain）：
    剩余预算不够等满间隔时，把等待**压进剩余预算**（宁可少等一点，也要把下一个池子试到），
    但至少留 GT_RETRY_MIN_LEFT 秒给这次请求 —— 真到点就放弃。
    ⚠️ 等待**计入调用方给的 deadline**（= GT 自己的子预算）：够不着就直接放弃，
    绝不让节流把整块结构预算睡掉 —— 这是「GT 那一档没时间发 OHLCV 请求」事故的一半原因。"""
    with _GT_LOCK:
        now = time.monotonic()
        wait = GT_MIN_INTERVAL - (now - _GT_LAST[0])
        if wait > 0:
            if deadline is not None:
                left = deadline - now
                if left <= (GT_RETRY_MIN_LEFT if clamp else 0.0):
                    return False
                if clamp:
                    wait = min(wait, left - GT_RETRY_MIN_LEFT)
            if wait > 0:
                time.sleep(wait)
        _GT_LAST[0] = time.monotonic()
    return True


def _gt_json(url, deadline=None, budget=None):
    """GeckoTerminal 专用 GET：**429 → 按 Retry-After（没有就 GT_429_BACKOFF_SEC）退避重试**。

    → (data, err, code, rate_limited, retry_after_s)；err / code / rate_limited 口径同 _http_json，
    retry_after_s 是上游 429 报文里的 Retry-After（没有 / 认不出 → None，**绝不编一个数**）。
    规则（改动②，2026-09-16 真机）：
      · 只对 **429** 退避（其它状态码一枪就回，不浪费时间）；最多再试 GT_429_RETRY_MAX 次
      · **有剩余预算才退避**：`left > wait + GT_RETRY_MIN_LEFT` 才睡；不够就立刻收手（返回 429）
      · 退避后仍要过 GT 的全局节流（间隔 ≥ GT_MIN_INTERVAL）——等待全部**计入 GT 自己的子预算**
      · 退避 / 重试的每一枪都走 _http_json(retry_429=False) → 这里说了算，不让底层偷偷睡 3 秒
    调用方拿到 429 之后负责文案与 kind=rate（GT_429_RATE_TEXT / GT_POOLS_429_RATE_TEXT）。
    ⚠️ 沙箱无网：真实 429 / Retry-After 报文**需在 host 真机验证**（离线用例只打桩/喂构造头）。"""
    retry_after_s = None
    out = (None, None, None, False)
    for attempt in range(GT_429_RETRY_MAX + 1):
        info = {}
        data, err, code, rl = _http_json(url, throttled=False, deadline=deadline, breaker=False,
                                         source="geckoterminal", budget=budget,
                                         retry_429=False, meta=info)
        out = (data, err, code, rl)
        got = _num(info.get("retry_after_s"))
        if got is not None:
            retry_after_s = got
        if code != 429:
            if code is not None:
                # 改动①：GT 答了话（哪怕是 404 / 5xx）= 上游活着 → 统一冷却立刻清零（自愈）
                _cb_note_ok("geckoterminal")
            return data, err, code, rl, retry_after_s
        if attempt >= GT_429_RETRY_MAX:
            break
        wait = retry_after_s if retry_after_s is not None else GT_429_BACKOFF_SEC
        if deadline is not None and (deadline - time.monotonic()) <= (wait + GT_RETRY_MIN_LEFT):
            break                        # 退避就要把预算睡光 → 别睡，直接把 429 交回（如实说）
        time.sleep(wait)
        if not _gt_slot(deadline):       # 退避之后照样要过全局节流；预算不够 → 收手
            break
    return out[0], out[1], out[2], out[3], retry_after_s


def _gt_pools_deadline(deadline):
    """GT 子预算里**先给「主池 OHLCV 这一枪」留出来**（GT_OHLCV_RESERVE_SEC）的那一份。

    列池子那一步（+它的 429 退避）最多用到这里，剩下的时间必须留给 OHLCV ——
    真机事故就是列池子把 5s 子预算全耗在退避上，池子找对了却一根日线都没取。
    deadline 已经很小 / 没有 deadline → 至少留 GT_RETRY_MIN_LEFT 秒给列池子那一步。"""
    if deadline is None:
        return None
    now = time.monotonic()
    return min(deadline, max(now + GT_RETRY_MIN_LEFT, deadline - GT_OHLCV_RESERVE_SEC))


def _bar_ts(v):
    """K 线时间戳 → 秒（Binance 给毫秒、GeckoTerminal 给秒）。"""
    n = _num(v)
    if n is None or n <= 0:
        return None
    return int(n / 1000.0) if n > 1e11 else int(n)


def _bar_date(b):
    ts = _dict(b).get("t")
    if not ts:
        return None
    try:
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None


def _bar_item(ts, o, h, l, c, v=None):
    """一根日线 → dict；任何价格字段解不出数字就整根丢掉（绝不把 None 当 0）。"""
    o, h, l, c = _num(o), _num(h), _num(l), _num(c)
    if o is None or h is None or l is None or c is None:
        return None
    return {"t": _bar_ts(ts), "o": o, "h": h, "l": l, "c": c, "v": _num(v)}


def _bars_sorted(rows, v_i=None):
    """K 线原始数组 → 按时间升序的 bars（不假设上游顺序：GT 是**倒序**返回的）。"""
    bars = []
    for row in _list(rows):
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        v = row[v_i] if (v_i is not None and len(row) > v_i) else None
        b = _bar_item(row[0], row[1], row[2], row[3], row[4], v)
        if b is not None:
            bars.append(b)
    bars.sort(key=lambda b: b["t"] or 0)
    return bars


def _struct_kind_of(text):
    """一句人话原因 → 结构取数里**这个源的状态**（决定文案说「源挂了」还是「没数据」）：
      · rate（限流 / 熔断 / 预算到点）· down（连不上 / 超时）= **源不可用**（都算「源挂了」）
      · nodata（上游答了话，但没有这个币的日线）= 有源通、只是没数据。
    沿用 missing 那套 `_err_kind` 口径，不另编一套分类。"""
    return {"熔断": "rate", "限流": "rate", "预算用完": "rate",
            "超时": "down", "连不上": "down"}.get(_err_kind(text), "nodata")


# 「源不可用」（熔断 / 限流 / 连不上）的 kind；文案要能一眼区分它和「有源通、只是没这个币」
_STRUCT_DOWN_KINDS = ("rate", "down")
# 「源答了话，但没有这个币的日线」的 kind
_STRUCT_EMPTY_KINDS = ("nodata", "notlisted")


def _bars_from_binance(symbol, deadline=None):
    """① Binance 现货日线 → (bars, err, kind)；kind ∈ ok / notlisted / nodata / rate / down / skip。

    HTTP 400 = 币安未上市（用户口径：这不是「失败」，是「没有这条交易对」）→ 交给第 ② 条。
    返回的 err 只说事实，调用方拼「日线不可得：…」时原样带上。
    ⚠️ symbol 一定是**代币**的 symbol（改动③：结果行的 symbol 取自代币对象 / baseToken，
    绝不是池名 "BNC4 / 4Stock"）；拼的就是这个 symbol 的 SYMBOLUSDT。
    ⚠️ 改动（2026-09-16 真机）：symbol 只准来自**确定的基础代币** —— 链上 RPC 的 symbol()
    （_evm_pool_sides）或 GT /tokens/{代币}；池名 / LP 名（_is_lp_token_name）**绝不参与**
    （真机事故：GT 限流时把报价侧 4Stock 当成基础代币 → 拿 4STOCK 拼币安，全线失败）。
    ⚠️ 预算口径（改动③）：这里是**结构取数**的子预算 → 超时文案读 STRUCT_BUDGET_SEC，
    绝不再拿行情那份 LOOKUP_BUDGET(12s) 冒充（面板上会显示过期数字）。"""
    sym = (_sym(symbol) or "").strip()
    if not sym:
        return None, "结果里没有 symbol，拼不出 Binance 交易对", "skip"
    data, err, code, rl = _http_json(URL_BINANCE_KLINES % urllib.parse.quote(sym),
                                     throttled=False, deadline=deadline, breaker=False,
                                     source="binance", budget=STRUCT_BUDGET_SEC)
    if code == 400:
        return None, "Binance 未上市（HTTP 400：%sUSDT 这条交易对不存在）" % sym, "notlisted"
    if not isinstance(data, list):
        why = err or "返回格式不是列表"
        # code is None = 连 HTTP 都没答（超时 / reset / 代理断）→ down；5xx 也算上游不可用
        kind = ("rate" if (rl or code == 429)
                else ("down" if (code is None or code >= 500) else "nodata"))
        return None, "Binance 日线取不到（%s）" % why, kind
    bars = _bars_sorted(data, v_i=5)      # Binance 第 6 列才是成交量
    if not bars:
        return None, "Binance 日线有效点不足（0 根）", "nodata"
    # ≥1 根就当候选（够不够 STRUCT_MIN_BARS(16) 根由 _struct_fetch 按**样本量**决定）：
    # 币安上架才 8 天的币不该因为「不足 16 根」就让整块结构盈亏比失败。
    return bars, None, "ok"


def _dex_pair(q, symbol, deadline=None):
    """DexScreener 找池子 → (pair, err)。优先 baseToken.symbol 与结果 symbol 相同，再按池子流动性挑。"""
    query = (q or "").strip() or (_sym(symbol) or "")
    if not query:
        return None, "没有合约地址 / symbol，DexScreener 无从搜起"
    # DexScreener 有**自己的**熔断桶：它连不上时只闸它自己，绝不连累 CoinGecko
    data, err, code, rl = _http_json(URL_DEXSCREEN_SEARCH % urllib.parse.quote(query),
                                     throttled=False, deadline=deadline,
                                     source="dexscreener", budget=STRUCT_BUDGET_SEC)
    pairs = [p for p in _list(_dict(data).get("pairs")) if isinstance(p, dict)]
    if not pairs:
        if rl or code == 429:
            return None, "DexScreener 限流(429)"
        return None, "DexScreener 没搜到交易对（%s）" % (err or "无结果")
    want = (_sym(symbol) or "").upper()

    def key(p):
        bt = _dict(p.get("baseToken"))
        same = 0 if (want and (_sym(bt.get("symbol")) or "").upper() == want) else 1
        liq = _num(_dict(p.get("liquidity")).get("usd")) or 0.0
        return (same, -liq)
    return sorted(pairs, key=key)[0], None


def _bars_from_geckoterminal(q, symbol, deadline=None):
    """② DexScreener 找池 → GeckoTerminal 日线 → (bars, err, kind)（kind 见 _struct_kind_of）。

    ⚠️ GT 日线只回溯 ~180 天（实测 184 根）→ 这条路算出来的高点只能叫 hi_180d，禁止叫 ATH。"""
    query = (q or "").strip() or (_sym(symbol) or "")
    pair, perr = _dex_pair(q, symbol, deadline)
    if pair is None:
        # 连锁都没得查（没地址也没 symbol）→ skip：既不是「源挂了」也不是「没这个币」
        return None, perr, ("skip" if not query else _struct_kind_of(perr))
    chain = (_str(pair.get("chainId")) or "").strip().lower()
    pool = (_str(pair.get("pairAddress")) or "").strip()
    net = GT_NETWORKS.get(chain, chain)
    if not net or not pool:
        return None, "DexScreener 结果里没有 chainId / pairAddress", "nodata"
    if not _gt_slot(deadline):
        return None, "GeckoTerminal 限流间隔还没到（本次查询预算已经不够等）", "rate"
    data, err, code, rl, _ra = _gt_json(URL_GT_OHLCV % (urllib.parse.quote(net), urllib.parse.quote(pool)),
                                        deadline=deadline, budget=STRUCT_BUDGET_SEC)
    attrs = _dict(_dict(data).get("data")).get("attributes") if isinstance(data, dict) else None
    if attrs is None and isinstance(data, dict):
        attrs = data.get("attributes")
    bars = _bars_sorted(_dict(attrs).get("ohlcv_list"), v_i=5)
    if not bars:
        if rl or code == 429:
            return None, GT_429_RATE_TEXT, "rate"
        if code == 404:
            return None, "GeckoTerminal 没有这个池子（HTTP 404：%s/%s）" % (net, pool), "nodata"
        # 连不上（code is None）也是「源不可用」，别混进「没这个币的日线」
        return None, "GeckoTerminal 日线取不到（%s）" % (err or ("HTTP %s" % code if code else "无数据")), \
            ("down" if code is None else "nodata")
    if not bars:
        return None, "GeckoTerminal 日线有效点不足（0 根）", "nodata"
    # ≥1 根就当候选；够不够 STRUCT_MIN_BARS(16) 根由 _struct_fetch 按样本量决定
    return bars, None, "ok"


def _gt_net_of_chain(chain):
    """结果行的链 → GeckoTerminal network id（GT 的 URL 只认自己这一套 network）。

    依次查：costbasis 的 CHAIN_INFO.gecko（ethereum→eth / polygon→polygon_pos / bsc→bsc）
    → DexScreener chainId 映射表 GT_NETWORKS（binance-smart-chain→bsc …）
    → GT 自己的 network（GT_NET_LABELS / GT_NET_GECKO_PLATFORM）。
    都认不出 → None（**绝不硬编一条链**：宁可这条腿跳过，让别人去试）。"""
    c = (_str(chain) or "").strip().lower()
    if not c:
        return None
    rec = _dict(_dict(getattr(COSTBASIS, "CHAIN_INFO", {})).get(c))
    geo = (_str(rec.get("gecko")) or "").strip().lower()
    if geo:
        return geo
    mapped = (_str(GT_NETWORKS.get(c)) or "").strip().lower()
    if mapped:
        return mapped
    if c in GT_NET_LABELS or c in GT_NET_GECKO_PLATFORM:
        return c
    return None


def _gt_token_pools(net, address, deadline=None, meta=None):
    """GT /networks/{net}/tokens/{addr}/pools?page=1 → **按 reserve_in_usd 降序**的池子列表。

    → (pools, err, kind)：pools = [(池地址, 流动性或 None), …]（大池在前）；
    kind ∈ ok / rate(429) / down(超时·5xx·连不上) / nodata(200 空) / notfound(404)（见改动④）。
    ⚠️ **429 ≠ 空**：nodata / notfound 是「拿到了确定回答：真没有池子」；rate / down 是「没能问出答案」，
    调用方（自证那条路）**绝不许**把后者当成「没有池子」。
    真机核过的字段（别猜）：data[].attributes.address（池地址）、reserve_in_usd（流动性）。
    429 → 走 _gt_json：有剩余预算就按 Retry-After 退避重试（最多 GT_429_RETRY_MAX 次）。
    meta（可选 dict）→ 写回 {"retry_after_s"}：调用的那一步（列池子）超时/限流时
    也能把上游给的 Retry-After 交给结构化字段 blocked_by。"""
    if _cb_is_open("geckoterminal"):
        return None, "GeckoTerminal 熔断中，本次没发请求（立刻跳下一个源）", "rate"
    if not _gt_call_ok():      # 改动③：本次查询的 GT 额度用完了 → 停手（kind=rate → 按已有结果降级）
        return None, "GeckoTerminal %s → 这一发自证没发" % _gt_capped_text(), "rate"
    if not _gt_slot(deadline):
        return None, "GeckoTerminal 限流间隔还没到（本次查询预算已经不够等）", "rate"
    url = URL_GT_TOKEN_POOLS % (urllib.parse.quote(net), urllib.parse.quote(address))
    data, err, code, rl, retry_after_s = _gt_json(url, deadline=deadline,
                                                  budget=STRUCT_BUDGET_SEC)
    if isinstance(meta, dict):
        meta["retry_after_s"] = retry_after_s
    pools = [p for p in _list(_dict(data).get("data")) if isinstance(p, dict)]
    if not pools:
        if rl or code == 429:
            return None, GT_POOLS_429_RATE_TEXT, "rate"
        if code == 404:
            return None, "GeckoTerminal 没有这个代币的池子（HTTP 404：%s/%s）" % (net, address), \
                "notfound"
        if code is None:
            # 连不上 = 源不可用，绝不混进「没这个币的日线」
            return None, "GeckoTerminal 池子列表取不到（连不上：%s）" % (err or "未知错误"), "down"
        if code is not None and 500 <= code < 600:
            # 5xx = 上游自己挂了（代理通、GT 答不了）→ 也是「没能问出答案」，不是「没有池子」
            return None, "GeckoTerminal 池子列表查询失败（HTTP %s：%s）" % (code, err or "上游错误"), \
                "down"
        return None, "GeckoTerminal 池子列表查询失败（%s）" % (err or "无数据"), \
            _struct_kind_of(err or "无数据")
    out, seen = [], set()
    for p in pools:
        attrs = _dict(_dict(p).get("attributes"))
        pa = (_str(attrs.get("address")) or "").strip()
        if not pa or pa in seen:
            continue
        seen.add(pa)
        out.append((pa, _num(attrs.get("reserve_in_usd"))))
    if not out:
        return None, "GeckoTerminal 池子列表里没给 attributes.address（池地址读不出来，不猜）", "nodata"
    out.sort(key=lambda t: (t[1] if t[1] is not None else -1.0), reverse=True)
    return out, None, "ok"


def _bars_from_geckoterminal_chain(chain, address, deadline=None):
    """③a **不依赖 GMGN / Binance** 的链上日线：GT 按「链 + 代币地址」取日线。

    ① GET /networks/{chain}/tokens/{token_address}/pools?page=1（**按 reserve_in_usd 降序**）
    ② 依次对前 GT_POOL_TRY_MAX(3) 个池子试 GET /networks/{chain}/pools/{pool}/ohlcv/day?limit=200
       —— **一个池子 404 / 空数据不代表这个币没日线**（真机：基础代币有 20 个池子，主池有日线），
       第一个出数据的池子胜出；试过的 (池子, 结果) 全部记进 attempts（→ meta.attempts）。
    ③ 全试完仍无数据 → 才报「这个币在 GeckoTerminal 没有日线」（试了哪些池子、各什么结果一条不少）
       404（池子级）**立刻换下一个池子**；429 则先按 Retry-After 退避重试（见 _gt_json），
       重试完还是 429 → kind=rate + 「稍后自动可查」（**不把剩下的额度烧在同一个源上**）。

    **预算优先级（2026-09-16 真机事故的根因）**：GT 子预算（默认 10s）里
    ① 列池子那一步最多用 `_gt_pools_deadline()`（= 子预算 − GT_OHLCV_RESERVE_SEC），
    ② 剩下的留给**主池 OHLCV 这一枪** —— 绝不让「列池子 + 429 退避」把 OHLCV 的时间睡掉。
    节流（_gt_slot）与 429 退避的等待**全部计入 GT 自己的子预算**。

    → (bars, err, kind, attempts)；kind 口径同 _struct_kind_of。
    ⚠️ GT 日线只回溯约 180 天（实测 184 根）→ 这条路的高点只能叫 hi_180d，**禁止叫 ATH**。
    只要结果行的 chain + token_address 都在，BSC / Base / ETH / SOL 都能走这条腿。"""
    net = _gt_net_of_chain(chain)
    addr = (_str(address) or "").strip()
    attempts = []

    def _att(pool, liq, bars_n, kind, reason, retry_after_s=None):
        return {"source": "geckoterminal", "leg": "chain_address", "pool": pool,
                "liquidity_usd": liq, "bars_n": bars_n, "kind": kind, "reason": reason,
                "retry_after_s": retry_after_s}

    if not net or not addr:
        return None, ("结果行里没有「链 + 合约地址」→ GT 按链+地址这条腿跳过"
                      "（不猜链、不猜地址）"), "skip", attempts
    pools_meta = {}
    pools, err, kind = _gt_token_pools(net, addr, _gt_pools_deadline(deadline), meta=pools_meta)
    if pools is None:
        if kind == "rate":
            # 列池子就限流/熔断 → 记一条（带上上游给的 Retry-After）交给结构化字段 blocked_by
            attempts.append(_att(None, None, None, "rate", err,
                                 retry_after_s=pools_meta.get("retry_after_s")))
        # 「列池子」那一枪的 kind 口径同自证（改动④：404 = notfound）；结构取数这边只认
        # rate / down / nodata（notfound 就是「这个代币没有池子」→ 归 nodata，别把新词漏下去）
        return None, err, ("nodata" if kind == "notfound" else kind), attempts
    todo = pools[:GT_POOL_TRY_MAX]
    for idx, (pool, liq) in enumerate(todo):
        if _cb_is_open("geckoterminal"):
            why = "GeckoTerminal 熔断中，这个池子没试"
            attempts.append(_att(pool, liq, None, "rate", why))
            return None, ("GeckoTerminal 熔断中，本次没发请求（按流动性试过 %d 个池子后停手）"
                          % idx), "rate", attempts
        if not _gt_slot(deadline, clamp=(idx > 0)):
            why = "GeckoTerminal 限流间隔/查询预算不够，这个池子没试"
            attempts.append(_att(pool, liq, None, "rate", why))
            return None, ("GeckoTerminal 子预算到点，没试完（按流动性试过 %d 个池子，"
                          "还有 %d 个没试）" % (idx, len(todo) - idx)), "rate", attempts
        url = URL_GT_TOKEN_OHLCV_DAY % (urllib.parse.quote(net), urllib.parse.quote(pool))
        data, err, code, rl, retry_after_s = _gt_json(url, deadline=deadline,
                                                      budget=STRUCT_BUDGET_SEC)
        attrs = _dict(_dict(data).get("data")).get("attributes") if isinstance(data, dict) else None
        if attrs is None and isinstance(data, dict):
            attrs = data.get("attributes")
        bars = _bars_sorted(_dict(attrs).get("ohlcv_list"), v_i=5)
        if bars:
            attempts.append(_att(pool, liq, len(bars), "ok", None))
            # ≥1 根就当候选；够不够 STRUCT_MIN_BARS(16) 根由 _struct_fetch 按样本量决定
            return bars, None, "ok", attempts
        # 这个池子没给日线：分清「池子空」和「源挂了 / 限流」——**只有前者才换下一个池子**
        if rl or code == 429:
            # 已经按 Retry-After 退避重试过了（见 _gt_json）→ 还是 429 就如实说，别再换池子烧额度
            why = "限流(429)" + ("，Retry-After %gs" % retry_after_s if retry_after_s else "")
            attempts.append(_att(pool, liq, 0, "rate", why, retry_after_s=retry_after_s))
            return None, GT_429_RATE_TEXT, "rate", attempts
        if code is None:
            attempts.append(_att(pool, liq, 0, "down", err or "连不上"))
            return None, "GeckoTerminal 日线取不到（%s）" % (err or "连不上"), "down", attempts
        why = ("HTTP 404（这个池子没日线）" if code == 404
               else ("HTTP %s" % code if code else "返回里没有 ohlcv_list（0 根）"))
        attempts.append(_att(pool, liq, 0, "nodata", why))
    detail = "；".join("%s %s" % (a["pool"], a["reason"]) for a in attempts) or "没试成"
    return None, ("这个币在 GeckoTerminal 没有日线（按流动性试过 %d 个池子：%s）"
                  % (len(attempts), detail)), "nodata", attempts


def _bars_from_gmgn(chain, address, deadline=None):
    """① GMGN market kline --resolution 1d → (bars, err, kind)（kind 见 _struct_kind_of）。

    任意链都有日线（**含 BSC / 币安未上市的链**）；一次查询只发 1 次 GMGN 请求（配额铁律在
    blood_chips_costbasis 那侧的统一闸门里）。⚠️ `time` 是**毫秒** → 必须走 _bar_item/_bar_ts
    的 ms 判定（真机踩过：币安毫秒被当秒 → year 55939 直接崩）。GMGN 的 volume 是 USD 成交额。"""
    cb = COSTBASIS
    if cb is None:
        return None, "GMGN 客户端不可用（costbasis 模块没读进来）", "skip"
    if not chain or not address:
        return None, ("结果行里没有「受支持的链 + 合约地址」→ 不知道拿哪条链的 GMGN 日线（不猜地址）"), "skip"
    g = cb.gmgn_chain_of(chain)
    if not g:
        return None, "GMGN 不支持 %s 这条链（支持：%s）" % (chain, "、".join(cb.gmgn_chains())), "skip"
    try:
        items, err = cb.gmgn_kline(chain, address, "1d")
    except Exception as exc:                                    # noqa: BLE001 - 取数失败不许炸整块
        return None, "GMGN 日线异常（%s: %s）" % (type(exc).__name__, exc), "down"
    if err:
        return None, "GMGN 日线取不到（%s）" % err, _struct_kind_of(err)
    bars = []
    for it in items:
        if not isinstance(it, dict):
            continue
        b = _bar_item(it.get("time"), it.get("open"), it.get("high"), it.get("low"),
                      it.get("close"), it.get("volume"))
        if b is not None:
            bars.append(b)
    bars.sort(key=lambda b: b["t"] or 0)
    if not bars:
        return None, "GMGN 日线有效点不足（0 根）", "nodata"
    # ≥1 根就当候选；够不够 STRUCT_MIN_BARS(16) 根由 _struct_fetch 按样本量决定
    return bars, None, "ok"


def _struct_src_deadline(deadline, src):
    """某个**源自己的子预算** deadline = min(总 deadline, now + STRUCT_SRC_BUDGET[src])。

    子预算本身再被 STRUCT_BUDGET_SEC 夹一次（env 给了个比总预算还大的值也不许越过总预算）。
    好处：一个源挂死 / 卡在慢连接上，最多吃掉自己那份，后面的源还有时间被问到。"""
    budget = min(float(STRUCT_SRC_BUDGET.get(src, STRUCT_BUDGET_SEC)), STRUCT_BUDGET_SEC)
    sub = time.monotonic() + budget
    return sub if deadline is None else min(deadline, sub)


def _struct_fetch(sym, q, deadline=None, chain=None, address=None, gt_blocked=None):
    """按顺序取日线 → (bars, meta, err, kind)；都拿不到时 err 逐条写清**每个源**为什么不行。

    源顺序（2026-09-16 起）：
      ① GMGN market kline 1d（任意链，含 BSC；封禁 / 熔断中 → **立刻跳过**，零请求零等待）
      ② Binance 现货 1d（币安上市币；HTTP 400 = 未上市 → 立刻下一档）
      ③ GeckoTerminal **两条腿**（共用一份 GT 子预算）：
         ③a 结果行的 chain + token_address（**不依赖 GMGN / Binance**，BSC / Base / ETH / SOL 都适用）：
            /networks/{chain}/tokens/{token_address}/pools?page=1（**按 reserve_in_usd 降序**）→
            **依次**对前 GT_POOL_TRY_MAX(3) 个池子试 ohlcv/day?limit=200
            （一个池子 404 / 空数据不代表这个币没日线；试过的 (池子, 结果) 进 meta.attempts）
         ③b 现有的按 symbol / 关键词找池（DexScreener → GT ohlcv/day）

    **样本口径（2026-09-16 修正：不再因为「只有 8 根」整块失败）**：
      · 某源 ≥ STRUCT_MIN_BARS(16) 根 = **可用** → 按顺序第一个可用的直接胜出（老行为不变）
      · 7–15 根 = **样本不足的候选**：记下来继续问后面的源；所有源都不够 16 根时用
        **根数最多**的那条（哪怕 <16 根）→ 照常算（新币口径 lo_recent），meta 写明样本量
      · 1–6 根 = 更短的候选：连 7 根都凑不齐才退回它，参照位 null + 原因（铁律不变）
      · 0 根 / 源挂了 = 不是候选，只记一条 attempts
    每条腿的 (源, 根数, kind, 原因) 都写进 meta.attempts —— 「有数据支撑不猜」。

    **每源独立子预算**（STRUCT_SRC_BUDGET，实际生效值见 struct_rr.debug.src_budget）
    —— 一个源挂死绝不把后面的源饿死；某个源熔断 / 限流就立刻跳下一个（不做无谓等待）。

    都拿不到时，**先说清是哪一种**（用户要能一眼区分「源挂了」和「没这个币」）：
      · 全部源都熔断 / 限流 / 连不上 → err 以「所有日线源都不可用」开头
      · 有源通了、只是没有这个币的日线 → err 以「查不到这个币的日线」开头
      · 结构取数**自己的预算**（STRUCT_BUDGET_SEC）到点 → err 以「超出结构取数预算」开头
    三种情况**都逐源列出原因**（「；」拼接，信息一句都不丢）。"""
    tries, kinds, attempts = [], [], []
    # 样本不足的**候补**（根数最多的那条）：所有源都够不到 STRUCT_MIN_BARS 时才退回它
    best = {"n": 0, "bars": None, "meta": None, "label": None, "src": None, "attempt": None}
    winner = []                       # 一旦有源够 STRUCT_MIN_BARS 根就填这里（闭包里当可变槽）

    def _label(src):
        return {"gmgn": "GMGN", "binance": "Binance",
                "geckoterminal": "GeckoTerminal"}.get(src, src)

    def _offer(src, bars, meta, err, kind, leg=None, extra=None, retry_after_s=None):
        """登记一条腿的结果：够 16 根 → 立刻胜出；不足 → 记成候补，继续问下一个源。
        extra：这条腿内部的**逐次尝试**（GT 按流动性换池子那几次），原样并进 attempts。
        retry_after_s：限流（kind=rate）时上游给的恢复时间（秒；拿不到就是 None）——
        它会顺着 attempts 汇总成 meta.blocked_by（前端只照抄，绝不自己编数字）。"""
        if winner:
            return
        for item in _list(extra):
            if isinstance(item, dict):
                attempts.append(dict(item))
        n = len(bars) if bars else 0
        if bars is None:
            attempts.append({"source": src, "leg": leg, "bars_n": None,
                             "kind": kind, "reason": err, "retry_after_s": retry_after_s})
            if err:
                tries.append(err)
            kinds.append(kind)
            return
        if n >= STRUCT_MIN_BARS:
            attempts.append({"source": src, "leg": leg, "bars_n": n,
                             "kind": "ok", "reason": None})
            winner.append((bars, meta, None, "ok"))
            return
        # 有这条交易对、只是根数不够 → 文案必须和「未上市」一眼可分（**不许**再写「未上市」）
        why = ("%s 有这条交易对，但只有 %d 根日线（上架时间短，不足 %d 根）"
               % (_label(src), n, STRUCT_MIN_BARS))
        entry = {"source": src, "leg": leg, "bars_n": n, "kind": "nodata", "reason": why}
        attempts.append(entry)
        tries.append(why)
        kinds.append("nodata")
        if n > best["n"]:
            best.update(n=n, bars=bars, meta=meta, label=_label(src), src=src, attempt=entry)

    def _finish(bars, meta):
        """胜出 → 把逐腿 attempts / 选源说明补进 meta（谁被跳过、为什么，一条不少）。"""
        out = dict(meta or {})
        out["attempts"] = [dict(a) for a in attempts]
        blocked = _struct_blocked_by(attempts)
        if blocked:
            out["blocked_by"] = blocked          # 成功也可能有源在限流（GMGN 常态）→ 如实带上
        out["choice_note"] = None
        if best["n"] and best["n"] < len(bars):
            out["choice_note"] = ("%s 上架仅 %d 天（%d 根日线），采用 %s 近 %d 根"
                                  % (best["label"], best["n"], best["n"],
                                     _label(_dict(out).get("source")), len(bars)))
        return bars, out, None, "ok"

    def _fallback():
        """所有源都不足 16 根 → 退回**根数最多**的那条（哪怕 <16 根），照常算。"""
        if not best["bars"]:
            return None
        if best["attempt"] is not None:
            best["attempt"]["kind"] = "ok"           # 这条腿被采用了
        out = dict(best["meta"] or {})
        out["attempts"] = [dict(a) for a in attempts]
        blocked = _struct_blocked_by(attempts)
        if blocked:
            out["blocked_by"] = blocked
        out["choice_note"] = ("所有日线源都拿不到 %d 根以上：采用根数最多的 %s（%d 根）"
                              % (STRUCT_MIN_BARS, best["label"], best["n"]))
        return best["bars"], out, None, "ok"

    # ① GMGN（第一顺位）：熔断 / 封禁中就直接跳过 —— 零请求、零等待
    if not _struct_source_up("gmgn"):
        ban = _gmgn_ban_info()                 # 报文里的解封时刻（读不到就当没有，绝不编）
        ban_left = _num(_dict(ban).get("left_s"))
        extra = ""
        if isinstance(ban, dict):
            extra = "（%s%s）" % (ban.get("why") or "熔断中",
                               "，约 %.0f 秒后解封" % ban_left if ban_left is not None else "")
        _offer("gmgn", None, None,
               "GMGN 日线取不到（GMGN 已熔断 / 封禁中%s，本次没发请求，等解封自动重试）" % extra,
               "rate", leg="kline", retry_after_s=ban_left)
    else:
        # 子预算**硬夹**：gmgn-cli 自己超时 30s，绝不能让它把整块结构预算吃光
        # （_cb_within_budget：daemon 线程 + join(子预算)，超了立刻撒手）
        gmgn_budget = min(float(STRUCT_SRC_BUDGET.get("gmgn", STRUCT_BUDGET_SEC)), STRUCT_BUDGET_SEC)
        if deadline is not None:
            gmgn_budget = min(gmgn_budget, max(0.0, deadline - time.monotonic()))
        res = _cb_within_budget(_bars_from_gmgn, gmgn_budget, chain, address)
        if isinstance(res, tuple) and len(res) == 3:
            bars, err, kind = res
        else:
            why = res[1] if (isinstance(res, tuple) and len(res) == 2) else "取数线程没有返回结果"
            bars, err, kind = None, "GMGN 日线取不到（%s）" % why, "rate"
        # kline 那一步也可能直接撞 RATE_LIMIT_BANNED（熔断器是这一枪之后才开的）→ 一样带解封时间
        _offer("gmgn", bars, {"source": "gmgn", "label": "GMGN 日线",
                              "note": ("GMGN market kline --resolution 1d（第三方口径；time 为毫秒已归一；"
                                       "volume 是 USD 成交额，用于量能对比）")}, err, kind, leg="kline",
               retry_after_s=(_gmgn_ban_left_s() if kind == "rate" else None))   # 被 RATE_LIMIT_BANNED 封了就有解封倒计时
    if winner:
        return _finish(winner[0][0], winner[0][1])

    # ② Binance（币安上市币）
    if sym:
        bars, err, kind = _bars_from_binance(sym, _struct_src_deadline(deadline, "binance"))
        _offer("binance", bars, {"source": "binance", "label": "Binance 日线",
                                 "note": "Binance 现货 1d（最多 400 根）"}, err, kind, leg="klines")
    else:
        tries.append("结果里没有 symbol，拼不出 Binance 交易对")
    if winner:
        return _finish(winner[0][0], winner[0][1])

    # ③ GeckoTerminal：两条腿**共用** GT 的子预算（默认 10s 里跑完，不额外多给）——
    #    ③a 里「列池子」只准用 `_gt_pools_deadline()`，**主池 OHLCV 那一枪**优先（见该函数）
    gt_deadline = _struct_src_deadline(deadline, "geckoterminal")
    # 改动③：**token_address 没定**（用户给的是池子、两侧代币都没自证通过）→ GT 两条腿**都不发**：
    #   拿池子地址 / LP 代币 / 池名去要日线只会白烧额度、误报 404（真机那次 CAKE-LP 就是这么来的）。
    if gt_blocked:
        _offer("geckoterminal", None, None,
               ("GeckoTerminal 熔断中：日线这一枪没发（%s）" % gt_blocked), "skip",
               leg="blocked")
    # ③a 按「链 + 合约地址」取日线（**不依赖 GMGN / Binance**；按流动性依次试前 3 个池子）
    elif _str(chain) and _str(address):
        # GT 正处于限流冷却中（_gt_cooling 是文件里既有的冷却判定）
        # → 这条腿**必然**失败，不许再打一次、更不许空等满 GT 子预算。
        # 熔断器（_cb_is_open）与冷却（_gt_cooling）**都要拦**：任一为真 → 零请求、立刻返回。
        # （codex 这轮只查了 _gt_cooling ✗ → 熔断中照样发枪，真机/单测都复现）
        if _gt_cooling("geckoterminal") or _cb_is_open("geckoterminal"):
            bars, gt_attempts = None, []   # None（不是 []）：_offer 才会把「熔断中…」如实记进 tries/error
            err = ("GeckoTerminal 熔断中：处于限流冷却中，本次跳过这条腿"
                   "（不空等；稍后自动可查）")
            kind = "rate"          # 跳过 = 被限流，不是「没有数据」
        else:
            bars, err, kind, gt_attempts = _bars_from_geckoterminal_chain(chain, address, gt_deadline)
        # 冷却中 GT 腿没被调用 → gt_attempts 为空数组，显式补一条如实记录（跳过 ≠ 没有数据）
        if _gt_cooling("geckoterminal") and not _list(gt_attempts):
            gt_attempts = [{"source": "geckoterminal", "leg": "ohlcv", "kind": "rate",
                            "reason": ("GeckoTerminal 熔断中：处于限流冷却中，"
                                       "本次跳过这条腿（不空等）")}]
        used_pool = None
        for a in _list(gt_attempts):
            if isinstance(a, dict) and a.get("kind") == "ok" and a.get("pool"):
                used_pool = a["pool"]
        _offer("geckoterminal", bars,
               {"source": "geckoterminal", "label": "GeckoTerminal(近180日)",
                "note": ("GeckoTerminal 日线只回溯约 180 天：高点是 hi_180d，不是真实 ATH"
                         "（这条按「链+地址」取日线：按流动性依次试前 %d 个池子，"
                         "不依赖 GMGN / Binance）" % GT_POOL_TRY_MAX),
                "pool": used_pool},
               err, kind, leg="chain_address", extra=gt_attempts)
    else:
        tries.append("结果行里没有「链 + 合约地址」→ GT 按链+地址那条腿跳过（不猜链 / 不猜地址）")
    if winner:
        return _finish(winner[0][0], winner[0][1])
    # ③b 按 symbol / 关键词找池（老路径保留）；改动③ 拦下时**这一腿也不发**
    if not gt_blocked:
        # 这条 symbol 版 GT 腿也要过**和 ③a 完全一样**的熔断/冷却闸门：
        # 熔断中 / 冷却中 → 零请求立刻返回（否则会白打一枪、还可能空等满 GT 子预算）。
        if _cb_is_open("geckoterminal") or _gt_cooling("geckoterminal"):
            bars = None           # None（不是 []）：_offer 才会把「熔断中…」如实记进 tries/error
            kind = "rate"          # 跳过 = 被限流，不是「没有数据」
            err = ("GeckoTerminal 熔断中：处于限流冷却中，本次跳过这条腿"
                   "（不空等；稍后自动可查）")
            _offer("geckoterminal", bars,
                   {"source": "geckoterminal", "label": "GeckoTerminal(近180日)",
                    "note": "GeckoTerminal 日线只回溯约 180 天：高点是 hi_180d，不是真实 ATH"},
                   err, kind, leg="ohlcv-symbol",
                   extra=[{"source": "geckoterminal", "leg": "ohlcv-symbol", "kind": "rate",
                           "reason": ("GeckoTerminal 熔断中：处于限流冷却中，"
                                      "本次跳过这条腿（不空等）")}])
        else:
            bars, err, kind = _bars_from_geckoterminal(q, sym, gt_deadline)
            _offer("geckoterminal", bars,
                   {"source": "geckoterminal", "label": "GeckoTerminal(近180日)",
                    "note": "GeckoTerminal 日线只回溯约 180 天：高点是 hi_180d，不是真实 ATH"},
                   err, kind, leg="symbol_search")
        # （原来这里 codex 又无条件 _offer 了一次同一份结果 —— 重复登记 ✗，已删）
        if winner:
            return _finish(winner[0][0], winner[0][1])
    # 所有源都不足 16 根 → 退回根数最多的那条（哪怕只有 7 根，甚至 1–6 根）
    got = _fallback()
    if got is not None:
        return got
    detail = "；".join([t for t in tries if t])
    # 有源答了话（nodata / notlisted）→ 「没有就是没有」；只有全是「源挂了」才算暂时性故障。
    # TTL 口径必须和下面的 lead 对齐（否则文案说「查不到这个币」，却按「源挂了」60 秒后重试）。
    has_empty = any(k in _STRUCT_EMPTY_KINDS for k in kinds)
    has_down = any(k in _STRUCT_DOWN_KINDS for k in kinds)
    # ⚠️ 只要**任何一条腿**是限流（rate），这次失败就是**暂时性的** → 60 秒后再试：
    #    真机口径（GMGN 长期被封 + GT 偶尔 429 + 币安未上市）若因为「未上市」就按「没有就是没有」
    #    缓存半小时，面板会把之后每一次查询都短路掉 —— GT 恢复了也照样查不到。
    #    那不是「没有」，是「等一会儿再来」（前端文案：稍后自动可查）。
    has_rate = any(k == "rate" for k in kinds)
    if deadline is not None and time.monotonic() >= deadline:
        # 预算到点：不是「源挂了」，也不是「没这个币」——是本次没来得及问完（如实说）
        lead = "超出结构取数预算：%s" % _struct_budget_detail()
    elif gt_blocked:
        # 改动③：没定出代币 → 先说清「为什么不拿池子地址 / LP 代币去要日线」
        lead = "没有确定的代币（%s）→ 没拿池子地址 / LP 代币去要日线" % gt_blocked
    elif has_empty or not has_down:
        # 有源答了话（或压根没问出去）→ 「没数据」，不是「源挂了」
        lead = "查不到这个币的日线（日线源是通的，只是没有这个币的日线）"
    else:
        lead = "所有日线源都不可用（熔断 / 限流 / 连不上）"
    # 失败也要**把试过的 (源腿 / 池子, 结果) 交出去**（→ meta.attempts）：用户要能自证「试过谁」。
    # 另外给一份**结构化**的 blocked_by（谁在限流 / 大概多久恢复）→ 前端只照抄，绝不自己编数字。
    meta_out = {"attempts": [dict(a) for a in attempts]}
    blocked = _struct_blocked_by(attempts)
    if blocked:
        meta_out["blocked_by"] = blocked
    return None, meta_out, \
        ("%s：%s" % (lead, detail) if detail else lead), \
        ("rate" if (has_rate or (has_down and not has_empty)) else "nodata")


def _gmgn_ban_info():
    """GMGN 熔断状态（costbasis 那侧读报文得到的 dict：why / left_s / until_iso…）。

    **只读状态、绝不发请求**（熔断期内零请求是铁律：探测会把封禁 +5s）。
    读不到 / 没熔断 → None（调用方按「不知道」处理，绝不编一个解封时间）。"""
    cb = COSTBASIS
    if cb is None:
        return None
    try:
        ban = cb.gmgn_ban_info()
    except Exception:                                           # noqa: BLE001 - 读不出就当没熔断
        return None
    return ban if isinstance(ban, dict) else None


def _gmgn_ban_left_s():
    """GMGN 报文的解封倒计时（秒）；没熔断 / 读不到 → None。"""
    return _num(_dict(_gmgn_ban_info()).get("left_s"))


def _struct_blocked_by(attempts):
    """attempts 里**限流（kind=rate）**那些腿 → blocked_by=[{source,kind,retry_after_s}]。

    这是给前端的**结构化**字段：前端只照抄（谁被限流 / 大概多久恢复），**绝不自己编数字**。
    · 按出现顺序去重（同一个源只出一条：GMGN 的 kline 腿、GT 的两条腿都算同一个源）
    · retry_after_s 取该源第一条带值的（上游 Retry-After / GMGN 报文的解封倒计时）；
      拿不到就是 None → 前端只写「限流中」，绝不写「约 N 秒」（没有数字就不给数字）"""
    out, seen = [], set()
    for a in _list(attempts):
        if not isinstance(a, dict) or _str(a.get("kind")) != "rate":
            continue
        src = _str(a.get("source")) or "?"
        if src in seen:
            continue
        live = _blocked_by_live({"source": src, "kind": "rate",
                                 "retry_after_s": a.get("retry_after_s")})
        if live is None:            # 改动③：GT 的限流结论过期了 → 不许再挂历史状态
            continue
        seen.add(src)
        out.append(live)
    return out


def _struct_blocked_by_sources():
    """**所有日线源都不可用**（一个请求都没发）时 → 逐源写 blocked_by（只写真的关着的源）。"""
    out = []
    for src in STRUCT_SOURCES:
        if _struct_source_up(src):
            continue
        out.append({"source": src, "kind": "rate",
                    "retry_after_s": (_gmgn_ban_left_s() if src == "gmgn" else None)})
    return out


def _struct_attempts_of(meta):
    """meta.attempts 原样取出（只留 dict）——**成功 / 失败都照给**（含 GT 换池子那几次尝试）。"""
    return [dict(a) for a in _list(_dict(meta).get("attempts")) if isinstance(a, dict)]


def _struct_fail_meta(meta):
    """失败时挂到 struct_rr.meta 上的东西：attempts（试过谁、各什么结果）+ blocked_by（谁在限流）。

    空 meta → None（响应里不出现 "meta": {} 这种没信息量的东西）。"""
    att = _struct_attempts_of(meta)
    if not att:
        return None
    out = {"attempts": att}
    blocked = _struct_blocked_by(att)
    if blocked:
        out["blocked_by"] = blocked
    return out


def _listing_age_days(bars, now=None):
    """币龄 = 首根 K 线至今多少天（`bars_n` 是**样本根数**，不是币龄）。拿不到首根时间戳 → None。"""
    ts = _num(_dict(bars[0]).get("t")) if bars else None
    if ts is None or ts <= 0:
        return None
    now = time.time() if now is None else now
    return max(0, int(round((now - ts) / 86400.0)))


def _struct_metrics(bars, now=None):
    """真实日线 → 结构数字。算不出来的**逐项记缺失 + 原因**（绝不默 0）。

    参照位跟**币龄**匹配（2026-09-15 真机：34 根日线的新币，lo_90d 抓到上市首根插针价
    →「到前低 −99.99%」失真；2026-09-16 复核：只剔首根也还是粉尘价，见模块顶部注释）：
    · bars_n < STRUCT_YOUNG_BARS(60)：下行参照位 = **近期窗口低点**，
      `w = min(max(STRUCT_YOUNG_RECENT_MIN(14), bars_n // 2), bars_n)`，取最近 w 根的**最低价**
      （34 根 → w=17；20 根 → w=14；10 根 → 14 被 bars_n 上限夹回 10）；
      字段：`lo_ref="lo_recent"` / `lo_ref_price` / `lo_ref_date` / `lo_ref_window` /
      `lo_window_label="近 %d 日最低（该币仅 %d 根日线）"`。
      上市以来最低（`lo_since_listing`）只当**参考信息**，**绝不参与** downside / rr 计算
      （新币早期几十根都是池子粉尘价，那不是支撑位）；缺口/反方理由里明写「样本不足」。
    · bars_n < STRUCT_YOUNG_MIN_BARS(7)：参照位直接 None + 原因（**绝不硬编一个数**）。
    · bars_n < STRUCT_MA_DAYS(200)：`ma200` 不叫 MA200，改叫「可用均线（N 根）」（N 进 `ma_window`）；
      bars_n < STRUCT_MIN_MA_BARS(30) → `ma200` 直接 None + 原因，**绝不硬算**。
    · bars_n ≥ 60：照旧用「近 90 日低点」（行为不变）。
    """
    closes = [b["c"] for b in bars]
    price = closes[-1]
    bars_n = len(bars)
    m = {"bars_n": bars_n, "price_close": price,
         "bars_start": _bar_date(bars[0]), "bars_end": _bar_date(bars[-1]),
         "listing_age_days": _listing_age_days(bars, now),
         "missing": {}}
    if not (price > 0):
        why = "日线最后一根收盘价不是正数（%s），结构数字都算不了" % price
        m["missing"]["price_close"] = why
        for k in ("upside_to_hi_pct", "downside_to_lo_pct", "rr_ratio", "ma200", "atr_pct",
                  "max_drop_90d_pct", "days_to_hi", "vol_trend_pct"):
            m["missing"][k] = why
        m.update(hi_180d=None, lo_90d=None, lo_ex_first_bar=None, lo_window_label=None,
                 lo_since_listing=None, lo_recent=None, notes=[],
                 lo_ref=None, lo_ref_price=None, lo_ref_date=None, lo_ref_window=None,
                 ma200=None, ma_window=None, ma_label=None,
                 upside_to_hi_pct=None, downside_to_lo_pct=None, rr_ratio=None, rr_note=None,
                 ma200_gap_pct=None, atr_pct=None, max_drop_90d_pct=None, days_to_hi=None,
                 vol_trend_pct=None)
        return m

    hi_slice = bars[-STRUCT_HI_DAYS:]
    hi = max(hi_slice, key=lambda b: b["h"])
    lo_slice = bars[-STRUCT_LO_DAYS:]
    lo = min(lo_slice, key=lambda b: b["l"])
    m.update(hi_180d=hi["h"], hi_180d_date=_bar_date(hi), hi_window_days=len(hi_slice),
             lo_90d=lo["l"], lo_90d_date=_bar_date(lo), lo_window_days=len(lo_slice))

    # 参考信息：**上市以来最低**（含首根）和「剔除首根」两个口径都留着备查 —— 只进参考信息区，
    # **绝不参与 downside / 结构比**（新币上市初期几十根都是池子粉尘价，那不是支撑位）。
    all_lo = min(bars, key=lambda b: b["l"])
    ex = list(bars[1:])
    ex_lo = min(ex, key=lambda b: b["l"]) if ex else None
    m.update(lo_since_listing=all_lo["l"], lo_since_listing_date=_bar_date(all_lo),
             lo_ex_first_bar=(ex_lo["l"] if ex_lo else None),
             lo_ex_first_bar_date=_bar_date(ex_lo) if ex_lo else None)

    # 下行参照位：**按币龄换口径**（口径确定性：注释 + 输出字段都写清）
    #   · 新币 bars_n < STRUCT_YOUNG_BARS(60)：近期窗口低点，w = min(max(14, bars_n // 2), bars_n)
    #     （34 根 → w=17 / 20 根 → w=14 / 10 根 → 14 受上限夹回 10）→ 最近 w 根的**最低价**
    #   · bars_n ≥ 60：照旧「近 90 日低点」（行为不变）
    #   · bars_n < STRUCT_YOUNG_MIN_BARS(7)：样本太短 → 参照位 None + 原因，**绝不硬编一个数**
    recent_w = min(max(STRUCT_YOUNG_RECENT_MIN, bars_n // 2), bars_n)
    recent_lo = min(bars[-recent_w:], key=lambda b: b["l"])
    m.update(lo_recent=recent_lo["l"], lo_recent_date=_bar_date(recent_lo))
    if bars_n < STRUCT_YOUNG_MIN_BARS:
        why = ("只有 %d 根日线，算不出有意义的下行参照位（不足 %d 根）"
               % (bars_n, STRUCT_YOUNG_MIN_BARS))
        m.update(lo_ref=None, lo_ref_price=None, lo_ref_date=None, lo_ref_window=None,
                 lo_window_label=None)
        m["missing"]["lo_ref_price"] = why
        m["missing"]["downside_to_lo_pct"] = why
        m["missing"]["rr_ratio"] = why
    elif bars_n < STRUCT_YOUNG_BARS:
        m.update(lo_ref="lo_recent", lo_ref_price=recent_lo["l"],
                 lo_ref_date=_bar_date(recent_lo), lo_ref_window=recent_w,
                 lo_window_label="近 %d 日最低（该币仅 %d 根日线）" % (recent_w, bars_n))
    else:
        m.update(lo_ref="lo_90d", lo_ref_price=m["lo_90d"], lo_ref_date=m.get("lo_90d_date"),
                 lo_ref_window=len(lo_slice),
                 lo_window_label="近 %d 日最低" % len(lo_slice))

    # 均线：**必须真有 200 根才配叫 MA200**；样本不足只给「可用均线（N 根）」；N<30 直接不算
    if bars_n >= STRUCT_MA_DAYS:
        m["ma200"] = sum(closes[-STRUCT_MA_DAYS:]) / float(STRUCT_MA_DAYS)
        m["ma_window"], m["ma_label"] = STRUCT_MA_DAYS, "MA200"
    elif bars_n >= STRUCT_MIN_MA_BARS:
        m["ma200"] = sum(closes) / float(bars_n)
        m["ma_window"], m["ma_label"] = bars_n, "可用均线（%d 根）" % bars_n
        m["missing"]["ma200"] = ("日线只有 %d 根（不足 %d 根）：这里给的是「可用均线（%d 根）」，"
                                 "不是 MA200" % (bars_n, STRUCT_MA_DAYS, bars_n))
    else:
        m["ma200"] = m["ma_window"] = m["ma_label"] = None
        m["missing"]["ma200"] = ("日线只有 %d 根（不足 %d 根），均线样本不足 → 不算"
                                 "（绝不硬凑一个数）" % (bars_n, STRUCT_MIN_MA_BARS))

    up = (m["hi_180d"] - price) / price * 100.0
    m["upside_to_hi_pct"] = up
    if m.get("lo_ref_price") is None:
        # 参照位都没有（日线太短）→ downside / 结构比一律 None，missing 里已经写明为什么
        m["downside_to_lo_pct"] = None
        m["rr_ratio"] = None
        m["rr_note"] = None
    else:
        dn = (price - m["lo_ref_price"]) / price * 100.0
        m["downside_to_lo_pct"] = dn
        if dn > 0:
            m["rr_ratio"] = up / dn
            m["rr_note"] = None
        else:
            # downside ≤ 0：现价就在参照低点（或更低）→ 没有下行参照位，比值没有意义，别硬除
            m["rr_ratio"] = None
            m["rr_note"] = "现价已在前低之下（无下行参照位）"
    m["ma200_gap_pct"] = ((price - m["ma200"]) / m["ma200"] * 100.0) if m["ma200"] else None
    m["ma200_state"] = None if m["ma200_gap_pct"] is None else (
        "在均线上方" if m["ma200_gap_pct"] >= 0 else "在均线下方")

    # ATR / 日均波动：TR = max(高-低, |高-前收|, |低-前收|)，取最近 N 根**简单平均**
    n = min(STRUCT_ATR_DAYS, len(bars) - 1)
    trs = []
    for i in range(len(bars) - n, len(bars)):
        pc = bars[i - 1]["c"]
        trs.append(max(bars[i]["h"] - bars[i]["l"],
                       abs(bars[i]["h"] - pc), abs(bars[i]["l"] - pc)))
    m["atr_days"] = n
    m["atr_pct"] = (sum(trs) / float(len(trs)) / price * 100.0) if trs else None
    if not m["atr_pct"]:
        m["missing"]["atr_pct"] = "ATR 算不出来（有效点不足或波幅为 0）"

    # 近 90 日最大单日跌幅（收盘对收盘）
    drops = []
    for i in range(max(1, len(closes) - STRUCT_LO_DAYS), len(closes)):
        if closes[i - 1] > 0:
            drops.append((closes[i] - closes[i - 1]) / closes[i - 1] * 100.0)
    m["max_drop_90d_pct"] = min(drops) if drops else None
    if m["max_drop_90d_pct"] is None:
        m["missing"]["max_drop_90d_pct"] = "日线不足 2 根，算不出单日跌幅"

    # Time：按日波动率把「回到前高」的距离外推成天数 —— 只是量级参考，不是预测
    if m["atr_pct"]:
        m["days_to_hi"] = max(0.0, up) / m["atr_pct"]
    else:
        m["days_to_hi"] = None
        m["missing"]["days_to_hi"] = "日波动率缺失，无法外推天数"

    # 量能：近 30 日 vs 前 30 日（同一个源、同一单位，比值里单位自己约掉）
    need = STRUCT_VOL_TREND_DAYS * 2
    vols = [b.get("v") for b in bars]
    if len(bars) >= need and all(v is not None for v in vols[-need:]):
        now30 = sum(vols[-STRUCT_VOL_TREND_DAYS:])
        prev30 = sum(vols[-need:-STRUCT_VOL_TREND_DAYS])
        if prev30 > 0:
            m["vol_trend_pct"] = (now30 - prev30) / prev30 * 100.0
        else:
            m["vol_trend_pct"] = None
            m["missing"]["vol_trend_pct"] = "前 30 日成交量合计为 0，比不出变化"
    else:
        m["vol_trend_pct"] = None
        m["missing"]["vol_trend_pct"] = ("日线只有 %d 根（不足 %d 根），近30日/前30日量能比不了"
                                         % (len(bars), need))
    m["vol_trend_state"] = None if m["vol_trend_pct"] is None else (
        "萎缩" if m["vol_trend_pct"] < 0 else "放大")
    # 参考信息（**绝不进 downside / 结构比算式**）：新币的「上市以来最低」是池子粉尘价，
    # 只能说明「上市初期池子多浅」，不能当支撑位 —— 前端把它放在参考信息位置显示。
    m["notes"] = (["上市以来最低仅作参考：新币早期是池子粉尘价，不能当支撑位"]
                  if bars_n < STRUCT_YOUNG_BARS else [])
    return m


def _pct_txt(v, signed=False):
    """百分比文案（1 位小数）；后端只在「反方理由」这类规则文案里用它。"""
    n = _num(v)
    if n is None:
        return None
    s = ("%+.1f" % n) if signed else ("%.1f" % n)
    return s + "%"


def _struct_reasons(m, row, siblings):
    """最强反方理由 —— 规则生成，但**每一条都必须引用真实数字**。"""
    out = []
    # 样本不足 = 最该先说的话（新币的「前高/前低」本身就不牢靠，别让用户拿结构比当结论）
    if _int(m.get("bars_n")) is not None and m["bars_n"] < STRUCT_YOUNG_BARS:
        txt = ("上市样本不足：仅 %d 根日线（不足 %d 根）→ 前高/前低参考价值有限，"
               "别拿结构比当结论" % (m["bars_n"], STRUCT_YOUNG_BARS))
        if m.get("lo_ref_window"):
            txt += "；新币下行参照位取最近 %d 根，样本仍不足，仅供参考" % m["lo_ref_window"]
        out.append(txt)
    if m.get("ma200_gap_pct") is not None and m["ma200_gap_pct"] < 0:
        out.append("现价在 %s 下方 %.1f%%（趋势未修复）"
                   % (m.get("ma_label") or "MA200", abs(m["ma200_gap_pct"])))
    vt = m.get("vol_trend_pct")
    if vt is not None and abs(vt) >= STRUCT_VOL_TREND_MIN_PCT:
        out.append("近 30 日成交量较前 30 日%s %.1f%%（%s）"
                   % (m.get("vol_trend_state") or "变化", abs(vt),
                      "缩量阴跌，没有承接" if vt < 0 else "放量，换手/分歧在变大"))
    if _num(row.get("tvl_usd")) is None:
        out.append("没有 TVL / 费用数据（只有一个价格，没有使用量支撑）")
    dd = _neg(row.get("drawdown_from_ath_pct"))
    if dd is not None:
        out.append("距 ATH 仍有 %.1f%%（深不代表到底，可能继续深）" % dd)
    want = (_sym(row.get("symbol")) or "").upper()
    dup = 0
    if want:
        for r in _list(siblings):
            r = _dict(r)
            if r is row or not r:
                continue
            if (_sym(r.get("symbol")) or "").upper() == want and _str(r.get("id")) != _str(row.get("id")):
                dup += 1
    if dup:
        out.append("同名仿盘/桥接版较多：本次结果里就有 %d 个同 symbol 的其它币" % dup)
    if not out:
        out.append("按现有数据没触发结构性反方项，但这不等于没有风险")
    return out


def _struct_payload(bars, meta, row, siblings):
    """结构盈亏比载荷（前端按行渲染；所有数字都来自真实日线）。"""
    m = _struct_metrics(bars)
    missing = dict(m.get("missing") or {})
    if m.get("rr_ratio") is None and m.get("rr_note") is None:
        # setdefault：日线太短时 _struct_metrics 已经写了更具体的原因，别拿这句话顶掉它
        missing.setdefault("rr_ratio", "下行参照位缺失，算不出比值")
    days = m.get("days_to_hi")
    if days is not None and m.get("atr_pct"):
        days_note = ("按过去 %d 天的日波动率（ATR%d/现价 %.1f%%）外推：只是量级参考，不是预测"
                     % (m.get("atr_days") or 0, m.get("atr_days") or 0, m["atr_pct"]))
    else:
        days_note = None
    gaps = ["筹码成本分布（需另跑带血筹码分析，本次没查）"]
    if (m.get("hi_window_days") or 0) < STRUCT_HI_DAYS:
        gaps.append("日线只有 %d 根（不足 %d 天）→ 上面的高点是 %d 日高点，不是真实 ATH"
                    % (m.get("hi_window_days") or 0, STRUCT_HI_DAYS, m.get("hi_window_days") or 0))
    if (m.get("bars_n") or 0) < STRUCT_YOUNG_BARS:
        gaps.append("该币仅 %d 根日线，样本不足，前高/前低参考价值有限" % m.get("bars_n"))
    if (m.get("bars_n") or 0) < STRUCT_MIN_BARS:
        gaps.append("样本仅 %d 根日线（不足 %d 根）：结构数字是**短样本口径**，仅供参考"
                    % (m.get("bars_n") or 0, STRUCT_MIN_BARS))
    if meta.get("source") == "gmgn":
        gaps.append("GMGN 日线是**第三方平台口径**（和 Binance / GeckoTerminal 的同一根日线"
                    "可能有微小差异）；一次查询只发 1 次 GMGN 请求")
    if meta.get("source") == "geckoterminal":
        gaps.append("GeckoTerminal 日线只回溯约 %d 天 → 上面的高点是 hi_180d，不是真实 ATH"
                    % (m.get("hi_window_days") or STRUCT_HI_DAYS))
    if _neg(_dict(row).get("drawdown_from_ath_pct")) is None:
        gaps.append("这条结果没有真实 ATH 口径（距ATH 缺失），ATH 相关的反方项没法判")
    # meta：**样本量 / 窗口 / 每条源腿的 (源, 根数, kind)** 都写出来（「有数据支撑不猜」）
    bars_n = m.get("bars_n") or 0
    if bars_n < STRUCT_MIN_BARS:
        sample_note = ("样本仅 %d 根日线（不足 %d 根）：按新币口径算，前高 / 前低参考价值有限"
                       % (bars_n, STRUCT_MIN_BARS))
    elif bars_n < STRUCT_YOUNG_BARS:
        sample_note = "样本仅 %d 根日线（不足 %d 根日线）" % (bars_n, STRUCT_YOUNG_BARS)
    else:
        sample_note = None
    if m.get("lo_ref_window"):
        lo_window_note = ("窗口 = 近 %d 日最低（该币仅 %d 根日线）"
                          % (m["lo_ref_window"], bars_n))
    elif m.get("lo_ref_price") is None:
        lo_window_note = ("窗口 = 无（样本仅 %d 根日线，不足 %d 根 → 参照位只给 null + 原因）"
                          % (bars_n, STRUCT_YOUNG_MIN_BARS))
    else:
        lo_window_note = "窗口 = 近 %d 日最低" % (m.get("lo_ref_window") or bars_n)
    meta_out = {
        "source": meta.get("source"), "label": meta.get("label"),
        # 这条日线**具体取自哪个池子**（GT 按流动性多池重试时才有值；别的源 null）
        "pool": meta.get("pool"),
        "bars_n": bars_n, "bars_end": m.get("bars_end"),
        "sample_note": sample_note, "lo_window_note": lo_window_note,
        "choice_note": meta.get("choice_note"), "note": meta.get("note"),
        "attempts": _struct_attempts_of(meta),
    }
    # 有源在限流（哪怕这次靠别的源出了数）→ 结构化字段照给：前端只照抄，绝不自己编数字
    blocked = [dict(b) for b in _list(meta.get("blocked_by")) if isinstance(b, dict)]
    if blocked:
        meta_out["blocked_by"] = blocked
    return {
        "ok": True,
        "caliber": "结构口径，非买卖建议",
        "meta": meta_out,
        "source": meta.get("source"),
        "source_label": meta.get("label"),
        "source_note": meta.get("note"),
        "bars_n": m.get("bars_n"),
        "listing_age_days": m.get("listing_age_days"),
        "bars_start": m.get("bars_start"),
        "bars_end": m.get("bars_end"),
        "price_close": m.get("price_close"),
        "price_close_date": m.get("bars_end"),
        "close_note": "现价取日线最后一根收盘（与卡片上的实时价口径可能略有差）",
        # 和卡片上的实时价**并排**给（两个口径不同：日线收盘 vs 实时快照，可能略有差）
        "live_price": _num(_dict(row).get("price_usd")),
        "live_price_note": "日线收盘 vs 实时：口径不同，可能略有差",
        "hi_180d": m.get("hi_180d"), "hi_180d_date": m.get("hi_180d_date"),
        "hi_window_days": m.get("hi_window_days"),
        "lo_90d": m.get("lo_90d"), "lo_90d_date": m.get("lo_90d_date"),
        "lo_window_days": m.get("lo_window_days"),
        # 下行参照位：**跟币龄匹配**（lo_ref 说清 downside 用的是哪一档：新币 = "lo_recent"
        # 「近期窗口低点」（w = lo_ref_window），≥60 根 = "lo_90d"「近 90 日低点」）
        "lo_window_label": m.get("lo_window_label"),
        "lo_ref": m.get("lo_ref"), "lo_ref_price": m.get("lo_ref_price"),
        "lo_ref_date": m.get("lo_ref_date"), "lo_ref_window": m.get("lo_ref_window"),
        "lo_recent": m.get("lo_recent"), "lo_recent_date": m.get("lo_recent_date"),
        # 参考信息（**绝不进 downside / 结构比算式**）：上市以来最低 = 新币早期的池子粉尘价
        "lo_since_listing": m.get("lo_since_listing"),
        "lo_since_listing_date": m.get("lo_since_listing_date"),
        "lo_ex_first_bar": m.get("lo_ex_first_bar"),
        "lo_ex_first_bar_date": m.get("lo_ex_first_bar_date"),
        "notes": m.get("notes") or [],
        # 均线：**只有真有 200 根才叫 MA200**（ma_window 说清实际用了多少根）
        "ma200": m.get("ma200"), "ma_window": m.get("ma_window"), "ma_label": m.get("ma_label"),
        "upside_to_hi_pct": m.get("upside_to_hi_pct"),
        "downside_to_lo_pct": m.get("downside_to_lo_pct"),
        "rr_ratio": m.get("rr_ratio"), "rr_note": m.get("rr_note"),
        "ma200_gap_pct": m.get("ma200_gap_pct"), "ma200_state": m.get("ma200_state"),
        "atr_pct": m.get("atr_pct"), "atr_days": m.get("atr_days"),
        "max_drop_90d_pct": m.get("max_drop_90d_pct"),
        "days_to_hi": days, "days_to_hi_note": days_note,
        "vol_trend_pct": m.get("vol_trend_pct"), "vol_trend_state": m.get("vol_trend_state"),
        "vol_trend_days": STRUCT_VOL_TREND_DAYS,
        "reasons": _struct_reasons(m, _dict(row), siblings),
        "gaps": gaps,
        "missing": missing,
    }


def _struct_key(row):
    """内存缓存的 key：优先币 id（换词查同一个币 = 零请求），退回 symbol。"""
    cid = _str(_dict(row).get("id"))
    if cid and cid.strip():
        return "id:" + cid.strip().lower()
    sym = _sym(_dict(row).get("symbol"))
    return ("sym:" + sym.lower()) if sym else None


def _struct_prune_locked(now):
    """内存上限 + 清掉早就过期的条目（只在持锁时调用）。"""
    for k in [k for k, v in _STRUCT_CACHE.items()
              if now - v["ts"] >= max(v["ttl"], STRUCT_NODATA_TTL)]:
        _STRUCT_CACHE.pop(k, None)
    if len(_STRUCT_CACHE) > STRUCT_CACHE_MAX:
        old = sorted(_STRUCT_CACHE.items(), key=lambda kv: kv[1]["ts"])
        for k, _v in old[:len(_STRUCT_CACHE) - STRUCT_CACHE_MAX]:
            _STRUCT_CACHE.pop(k, None)


def _struct_source_up(src):
    """某个**日线候选源**现在还能不能问（False = 这个源已熔断/封禁/读不到）。

    · gmgn：它的熔断在 costbasis 那侧（RATE_LIMIT_BANNED → 按报文里的解封时刻熔断 + 零请求），
      用 gmgn_ban_info() 判定；判定不了（模块读不到）才算「不能用」。
    · 其余（binance / dexscreener / geckoterminal）：各看**自己的**熔断桶（_cb_is_open）。
      ⚠️ Binance 的日线请求不吃代理熔断闸门（_bars_from_binance 里 breaker=False），
      所以它的桶平时是关着的 → 这里按「还能问」算：**宁可多试一次，也不许连坐跳过**。"""
    if src == "gmgn":
        cb = COSTBASIS
        if cb is None:
            return False
        try:
            return cb.gmgn_ban_info() is None
        except Exception:                                       # noqa: BLE001 - 判定不了就别拦
            return True
    return not _cb_is_open(src)


def _struct_sources_all_down():
    """**所有**日线候选源都不可用 → True（只有这时才允许跳过结构盈亏比）。
    只要还有一个能问（尤其 GMGN）就必须照常发请求：别的源挂了**绝不连坐**。"""
    return not any(_struct_source_up(s) for s in STRUCT_SOURCES)


def _struct_ident(row):
    """「取哪一份日线」的身份：(symbol, chain, address, gt_blocked)。

    并行取数时行情侧会**先**给出第 1 条、稍后再补 id / 市值 / 距ATH —— 所以挂结果前拿这个
    身份核对「取的是不是同一条」，对不上就回落到同步路径（宁可重取一次，也绝不挂错币）。
    gt_blocked：**改动③** 的口径 —— 结果行明说「用户给的是池子、但没定出代币」时是那句原因，
    下游的 GT 日线**两条腿都不发**（拿池子地址 / LP 代币 / 池名去要日线只会白烧额度 + 误报 404）；
    没有这条限制 → None。"""
    r = _dict(row)
    hit = _cb_row_contract(r)
    chain, address = (hit[0], hit[1]) if hit else (None, None)
    return (_sym(r.get("symbol")), _str(chain), _str(address), _str(r.get("gt_blocked")))


def _struct_compute(row, q, siblings, deadline, t_start=None):
    """真取数（GMGN → Binance → GT）+ 组 payload → (payload, ttl)。**不看缓存**。

    同步路径（build_struct_rr）：身份本地就有，不需要等交接 → timings 里 t_handoff = t_struct_begin
    （和并行路径同一个格式，便于对照）。"""
    t0 = time.monotonic() if t_start is None else float(t_start)
    sym, chain, address, gt_blocked = _struct_ident(row)
    t_begin = time.monotonic()
    bars, meta, err, kind = _struct_fetch(sym, q, deadline, chain=chain, address=address,
                                          gt_blocked=gt_blocked)
    t_end = time.monotonic()
    timings = _struct_timings(t0, t_begin, t_begin, t_end, struct_deadline=deadline)
    if bars is None:
        body = {"ok": False, "error": "日线不可得：%s" % (err or "未知原因"),
                "debug": _struct_debug(timings)}
        meta_out = _struct_fail_meta(meta)   # 逐源腿 / 逐池子试过什么 + 谁在限流，失败时也要看得见
        if meta_out:
            body["meta"] = meta_out
        return body, (STRUCT_FAIL_TTL if kind == "rate" else STRUCT_NODATA_TTL)
    body = _struct_payload(bars, meta, row, siblings)
    body["debug"] = _struct_debug(timings)
    return body, STRUCT_TTL


def _struct_cache_put(key, payload, ttl, now=None):
    """⛔ 只放内存（_STRUCT_CACHE），一个文件都不落。"""
    if not key:
        return
    now = time.time() if now is None else now
    with _STRUCT_LOCK:
        _STRUCT_CACHE[key] = {"ts": now, "payload": payload, "ttl": ttl}
        _struct_prune_locked(now)


class _StructSlot(object):
    """行情侧 → 并行的结构取数任务的**一次性交接点**：第 1 条到底是谁。

    · 行情富化（找币 / 补行情）一确定第 1 条的身份就 put(row) → 结构取数（GMGN → Binance →
      GT）立刻开跑，两边**并行**、**各算各的预算**（行情慢到爆也不把结构饿死）。
    · close()：行情侧提前返回 / 抛异常时的兜底放行 —— 结构任务绝不许把请求吊死
      （拿不到身份就返回 None，调用方走原来的同步路径）。
    · 用 Event + 每轮 ≤50ms 的带超时等待（绝不在持锁时睡觉，也绝不无限期挂着）。
    """

    def __init__(self):
        self.row = None
        self._ev = threading.Event()
        self._closed = threading.Event()

    def put(self, row):
        """第 1 条的身份（**只认第一次**：先到的那条就是最终的第 1 条）。不是 dict 就忽略。"""
        if self.row is None and isinstance(row, dict):
            self.row = dict(row)
            self._ev.set()

    def close(self):
        self._closed.set()

    def wait(self, deadline):
        """等第 1 条身份（最多等到 deadline，或 close 为止）→ dict / None。"""
        while True:
            if self._ev.is_set():
                return self.row
            if self._closed.is_set():
                return None
            left = None if deadline is None else deadline - time.monotonic()
            if left is not None and left <= 0:
                return None
            self._ev.wait(0.05 if left is None else max(0.001, min(0.05, left)))


# 行情侧 → 结构取数任务的交接点。用 **thread-local** 而不是全局变量：
# ThreadingHTTPServer 每个请求一个线程，并发查询绝不互相串味。
_STRUCT_ROW_SINK = threading.local()


def _publish_struct_row(row):
    """行情侧：第 1 条的身份一确定就交给并行的结构取数任务（没人在等 → 什么都不做）。"""
    sink = getattr(_STRUCT_ROW_SINK, "sink", None)
    if sink is not None:
        sink(row)


def _struct_prefetch(slot, q, t_start=None, hard_deadline=None):
    """并行任务：等行情侧定下「第 1 条是谁」→ 立刻取日线 → 把**原始结果**交回主线程。

    ⚠️ 结构取数**自己的 deadline 从交接点起算**：`t_handoff + STRUCT_BUDGET_SEC`。
    老口径「请求开始 + STRUCT_BUDGET_SEC」在行情侧慢（交接晚到 10+ 秒）时，结构一动手就已经
    「超预算」→ 直接失败（2026-09-16 btc 12.8s 事故：源各自 <1s，却整条 lookup 12.8s 才失败）。
    交接**始终没来**（行情侧挂了）→ `slot.wait` 最多等到 `t_start + LOOKUP_TOTAL_TIMEOUT_SEC`
    兜底，绝不无限等。

    ⚠️ 这里**只取数、不组 payload**：`_struct_payload` 要用第 1 条的实时价 / 距ATH / TVL 和
    「同名仿盘」计数，那些得等行情富化补完才是最终值（否则结构块会平白少字段）。
    返回（都带 "ident" 供主线程核对是不是同一条币）：
      · {"bars":…, "meta":…, "err":…, "kind":…, "timings":…} 取数结果（bars=None 就是失败）
      · {"cached":…}             内存缓存命中（零请求）
      · {"allsourcedown": True}  所有日线源都挂了 → 交给 _attach_struct 走「本次没发请求」
      · {"ident": None, …}       行情侧始终没给身份（0 条结果 / 替身实现 / 提前返回）
    ⛔ 结果只放内存（最终由 _attach_struct_prefetched 落到 _STRUCT_CACHE）。"""
    t0 = time.monotonic() if t_start is None else float(t_start)
    if hard_deadline is None:
        hard_deadline = t0 + LOOKUP_TOTAL_TIMEOUT_SEC
    row = slot.wait(hard_deadline)                 # 交接始终没来 → 用总超时兜底，不无限等
    t_handoff = time.monotonic()
    # ① 结构预算从**交接点**起算（拿到身份那一刻），再被整体上限夹一次（响应绝不越过 LOOKUP_TOTAL_TIMEOUT_SEC）
    struct_deadline = min(t_handoff + STRUCT_BUDGET_SEC, hard_deadline)

    def _timings(begin=None, end=None):
        return _struct_timings(t0, t_handoff, begin, end, struct_deadline=struct_deadline)

    if row is None:
        return {"ident": None, "noident": True, "timings": _timings()}
    ident = _struct_ident(row)
    t_begin = time.monotonic()
    # 全部日线源都挂了 → **一个上游请求都不发**（和 _attach_struct 的 allow_fetch=False 同一口径：
    # 有缓存吃缓存，没有就写「本次没发请求」）。判断放在取数**之前**，否则 GMGN/Binance 那一步
    # 可能已经把请求发出去了。
    if _struct_sources_all_down():
        return {"ident": ident, "allsourcedown": True, "timings": _timings(t_begin)}
    key = _struct_key(row)
    # 缓存只认「按币 id」的 key：id 缺失时 key 会退化成 symbol，而 symbol 可能撞车
    # （同名仿盘），并行路径上宁可多打一次日线，也绝不把别的币的日线挂上来。
    if key and key.startswith("id:"):
        with _STRUCT_LOCK:
            rec = _STRUCT_CACHE.get(key)
        if rec and time.time() - rec["ts"] < rec["ttl"]:
            return {"ident": ident, "cached": rec["payload"], "timings": _timings(t_begin)}
    if time.monotonic() >= struct_deadline:
        return {"ident": ident, "bars": None, "meta": None, "kind": "rate",
                "err": "超出结构取数预算：%s" % _struct_budget_detail(),
                "timings": _timings(t_begin)}
    bars, meta, err, kind = _struct_fetch(ident[0], q, struct_deadline,
                                          chain=ident[1], address=ident[2], gt_blocked=ident[3])
    return {"ident": ident, "bars": bars, "meta": meta, "err": err, "kind": kind,
            "timings": _timings(t_begin, time.monotonic())}


def _struct_future_result(fut, deadline):
    """取并行任务的结果（最多等到它的 deadline，绝不把响应拖过总上限）。
    并行任务自己炸了 / 超时 → None（调用方回落到同步路径，绝不让整个查询 500）。"""
    if deadline is not None:
        left = deadline - time.monotonic()
        if left <= 0:
            return None
    else:
        left = None
    try:
        return fut.result(timeout=left)
    except Exception:                                           # noqa: BLE001 - 并行失败不拖垮响应
        return None


def build_struct_rr(row, q=None, siblings=None, deadline=None):
    """查询结果**第 1 条**的结构盈亏比载荷（**同步**路径：/api/lookup 之外、熔断降级时用）。

    结构口径，非买卖建议。⛔ 结果只放内存（_STRUCT_CACHE），一个文件都不落。
    取不到日线 → {"ok": False, "error": "日线不可得：<原因>"}，前端整块照写原因，绝不默 0。
    deadline 是**结构取数自己的**预算（STRUCT_BUDGET_SEC），和行情富化那份互不连坐。"""
    row = _dict(row)
    key = _struct_key(row)
    now = time.time()
    if key:
        with _STRUCT_LOCK:
            rec = _STRUCT_CACHE.get(key)
        if rec and now - rec["ts"] < rec["ttl"]:
            return rec["payload"]
    if deadline is not None and time.monotonic() >= deadline:
        return {"ok": False,
                "error": "日线不可得：超出本次查询预算，也就是超出结构取数预算：%s"
                         % _struct_budget_detail(),
                "debug": _struct_debug(_struct_timings(time.monotonic(), None, None, None))}
    payload, ttl = _struct_compute(row, q, siblings, deadline, t_start=time.monotonic())
    _struct_cache_put(key, payload, ttl, now)
    return payload


def _merge_row_blocked_by(row, body):
    """改动⑥：把结果行自带的结构化 blocked_by（如 GT **自证**那一枪被 429 挡住）并进
    struct_rr.meta.blocked_by —— 面板那句「GeckoTerminal 限流中」照旧只读这一个字段，
    **前端不新增任何逻辑**。

    按 (source, kind) 去重（不覆盖原来已有的条目）；body 没换内容就原样返回（省一次拷贝）。
    ⛔ 绝不改缓存里那份 payload（要改先浅拷一层 dict）。"""
    extra = [live for live in (_blocked_by_live(b)
                              for b in _list(_dict(row).get("blocked_by"))
                              if isinstance(b, dict)) if live is not None]
    if not extra or not isinstance(body, dict):
        return body
    meta = dict(_dict(body.get("meta")))
    merged = [live for live in (_blocked_by_live(b)
                                for b in _list(meta.get("blocked_by"))
                                if isinstance(b, dict)) if live is not None]
    seen = {(_str(_dict(b).get("source")) or "?", _str(_dict(b).get("kind")) or "rate")
            for b in merged}
    added = False
    for b in extra:
        key = (_str(b.get("source")) or "?", _str(b.get("kind")) or "rate")
        if key in seen:
            continue
        seen.add(key)
        merged.append(b)
        added = True
    if not added:
        return body
    meta["blocked_by"] = merged
    out = dict(body)
    out["meta"] = meta
    return out


def _attach_struct(payload, q, deadline, allow_fetch=True, timings=None):
    """把结构盈亏比挂到**第 1 条**结果上（其余结果保持现状）。

    allow_fetch=False（只有**所有日线源都挂了**才会这样）只吃内存缓存：有就照给，
    没有就写清「所有日线源都熔断中，本次没发请求」。
    ⛔ 这里一个文件都不写。"""
    rows = _list(_dict(payload).get("results"))
    if not rows or not isinstance(rows[0], dict):
        return payload
    row = rows[0]
    # ⚠️ 2026-09-15 真机事故（PEPE）：CoinGecko / DexScreener 熔断，但 **GMGN 完全健康**，
    #    而当时这里只看「coinGecko 或 dexscreener 熔断」就把整块结构盈亏比跳过 —— 连第一顺位的
    #    GMGN 都没问。那是「别的源挂了 → 连 GMGN 也不问」的**连坐** bug，注释里那句
    #    「结构取数走 Binance / DexScreener / GeckoTerminal」也早就过期了。
    #    现在：**只有所有日线候选源全挂才停手**；只要还有一个源能问（尤其 GMGN）就照常
    #    build_struct_rr —— _struct_fetch 内部按 GMGN → Binance → GT 顺序逐源试，
    #    各源自己的熔断由各自桶管（GMGN 的封禁在 costbasis 那侧的 gmgn_ban_info 里）。
    if allow_fetch and _struct_sources_all_down():
        allow_fetch = False
    if allow_fetch:
        row["struct_rr"] = _merge_row_blocked_by(
            row, build_struct_rr(row, q=q, siblings=rows, deadline=deadline))
        return payload
    key = _struct_key(row)
    rec = None
    if key:
        with _STRUCT_LOCK:
            rec = _STRUCT_CACHE.get(key)
    if rec and time.time() - rec["ts"] < rec["ttl"]:
        row["struct_rr"] = _merge_row_blocked_by(row, rec["payload"])
    else:
        body = {"ok": False,
                "error": "日线不可得：所有日线源都熔断中（%s），本次没发请求"
                         % "、".join(STRUCT_SOURCE_LABELS),
                "debug": _struct_debug(timings)}
        blocked = _struct_blocked_by_sources()      # 谁关着、GMGN 大概多久解封（结构化字段）
        if blocked:
            body["meta"] = {"attempts": [], "blocked_by": blocked}
        row["struct_rr"] = body
    return payload


def _attach_struct_prefetched(payload, q, fut, deadline):
    """把**并行取好**的日线挂到第 1 条（行情富化那边已经跑完了）。

    身份对得上（`_struct_ident` 相同）→ 用并行的结果组 payload（此时第 1 条的实时价 / 距ATH /
    TVL 和整行列表都是**最终值**，字段一个不少）；对不上 / 任务没结果 → 回落到同步的
    `_attach_struct`（行为与老路径一字不差）。
    ⛔ 这里一个文件都不写（只进 _STRUCT_CACHE 内存）。"""
    rows = _list(_dict(payload).get("results"))
    if not rows or not isinstance(rows[0], dict):
        return payload
    row = rows[0]
    res = _struct_future_result(fut, deadline)
    if (not res or res.get("ident") != _struct_ident(row)
            or res.get("allsourcedown")):
        # 回落同步路径也把并行的 timings 带上（诊断不丢）；没有 timings 就照老样子
        return _attach_struct(payload, q, deadline, timings=(res or {}).get("timings"))
    if "cached" in res:
        # 缓存命中：**原样复用**那份 payload（它自带算好那次的 debug；同一币再查必须逐字段一致）
        body = res["cached"]
    elif res.get("bars") is None:
        body = {"ok": False, "error": "日线不可得：%s" % (res.get("err") or "未知原因"),
                "debug": _struct_debug(res.get("timings"))}
        meta_out = _struct_fail_meta(res.get("meta"))   # + blocked_by（谁在限流、多久恢复）
        if meta_out:
            body["meta"] = meta_out
        _struct_cache_put(_struct_key(row), body,
                          STRUCT_FAIL_TTL if res.get("kind") == "rate" else STRUCT_NODATA_TTL)
    else:
        body = _struct_payload(res["bars"], res["meta"], row, rows)
        body["debug"] = _struct_debug(res.get("timings"))
        _struct_cache_put(_struct_key(row), body, STRUCT_TTL)
    row["struct_rr"] = _merge_row_blocked_by(row, body)
    return payload


# ============================================================ 带血筹码 · 成本分布（面板接入）
# 用户点「算这个币的带血筹码」→ 面板里跑 blood_chips_costbasis 的分析：
#   ① GET  /api/costbasis/resolve?q=       解析 (chain, address)：0x 地址走 DexScreener 反查链；
#     币名/代码**复用 /api/lookup 的同一套排序**（市值排名 + 桥接/包装版垫底）拿第 1 条，
#     再 GeckoTerminal /search/pools（按流动性）→ 最后 CoinGecko platforms；
#     选不到就 ok:false + 写明**每条路试过什么**（**绝不瞎猜链、绝不拿搜索第 2 条**）。
#     ⚠️ 前端会**先**调它把「将要分析：币名 · 链 · 地址缩写 · 来源」显示出来，再起任务。
#   ② POST /api/costbasis/start  {q} 或 {chain,address}(+symbol/name) → {job_id}；同一币已有结果/在跑就复用，
#     **同一进程最多 1 个任务在跑**，多的排队（单 worker 线程）。优先 import costbasis 的 analyze()
#     （不用 CLI 子进程：省一次进程 + 结果直接进内存）。前端把刚解析出的 chain+address 一起带上，
#     保证「显示的那个币 == 分析的那个币」。
#   ③ GET  /api/costbasis/status?job_id=  → {state: running|done|error, progress, result, why}
# ⛔ 结果只在内存（_COSTBASIS_CACHE，TTL 15 分钟）：一个文件都不落（和 MARKET_CACHE_PERSIST 同一条铁律）。
COSTBASIS_STAGE_TEXT = {
    "start": "拉起分析…",
    "token": "拉代币元数据…",
    "price": "拉价格…",
    "holders": "拉大户…",
    "transfers": "拉流水…",
    "retry": "补漏 pass…",
    "aggregate": "汇总…",
    "done": "汇总完成…",
}

_COSTBASIS_LOCK = threading.RLock()
_COSTBASIS_CACHE = {}        # job_id -> {"ts","result","chain","address","symbol"}（只放内存）
_COSTBASIS_JOBS = {}         # job_id -> 任务状态（queued/running/done/error）
_COSTBASIS_QUEUE = []        # 排队中的 job_id（FIFO）
_COSTBASIS_WORKER = [None]   # 唯一的 worker 线程（单任务串行）


def _load_costbasis_module():
    """import 同目录的 blood_chips_costbasis（拿不到也不致命：路由会写清原因，面板其它功能照常）。"""
    try:
        import blood_chips_costbasis as cb                      # 正常起服务时脚本目录就在 sys.path
        return cb
    except Exception:                                           # noqa: BLE001
        try:
            import importlib.util
            path = os.path.join(BASE, "blood_chips_costbasis.py")
            spec = importlib.util.spec_from_file_location("blood_chips_costbasis", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        except Exception:                                       # noqa: BLE001
            return None


COSTBASIS = _load_costbasis_module()


def _cb_chains():
    """costbasis 支持（= 有 CHAIN_INFO）的链；模块不可用时退化成面板里的优先级表。"""
    info = getattr(COSTBASIS, "CHAIN_INFO", None)
    return tuple(sorted(info)) if isinstance(info, dict) and info else COSTBASIS_CHAIN_PRIORITY


def _cb_net_addr(chain, address):
    """地址归一：Solana 原样（大小写敏感），其余小写。"""
    a = (_str(address) or "").strip()
    return a if chain == "solana" else a.lower()


def _cb_job_id(chain, address):
    raw = "%s|%s" % (chain, _cb_net_addr(chain, address))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _cb_ban_until():
    """GMGN 熔断的解封时刻（ISO8601，本机时区）；**没熔断 / 读不到 → None**（绝不编一个时间）。

    ⚠️ 只读状态：**绝不因为要看时间就发请求** —— 熔断期内零请求是铁律（探测会把封禁 +5s）。"""
    cb = COSTBASIS
    if cb is None:
        return None
    try:
        ban = cb.gmgn_ban_info()
    except Exception:                                           # noqa: BLE001 - 读不到就当没熔断
        return None
    return (_str(_dict(ban).get("until_iso")) or None) if ban else None


def _cb_ban_hhmm(iso):
    """ISO 时间戳 →「HH:MM」（面板文案用）；取不到 → 空串。"""
    m = re.search(r"T(\d{2}):(\d{2})", _str(iso) or "")
    return ("%s:%s" % (m.group(1), m.group(2))) if m else ""


def _cb_ban_note(iso):
    """熔断中给用户看的一句话（前端同一口径）：说清「限流中 / 预计 HH:MM 恢复 / 别的源先顶着」。"""
    hhmm = _cb_ban_hhmm(iso)
    when = ("预计 %s 恢复" % hhmm) if hhmm else "等它自己解封"
    return ("GMGN search（限流）：GMGN 限流中，%s（用其它源先顶着；熔断期零请求，"
            "绝不探测式重试 —— 探测会把封禁续期）" % when)


def _cb_fail(why, **extra):
    """解析失败统一形状：ok:false + 写明原因（chain/address/symbol 一律 null，绝不瞎猜）。
    resolved_* 也一律带上（能自证「我到底解析到了谁」），拿不到就 null。"""
    out = {"ok": False, "chain": None, "address": None, "symbol": None,
           "resolved_name": None, "resolved_chain": None, "resolved_address": None,
           "resolved_rank": None, "resolved_liquidity_usd": None, "source": None,
           # GMGN 正在熔断时给解封时刻（前端显示「预计 HH:MM 恢复」）；没熔断 → None
           "gmgn_ban_until": _cb_ban_until(),
           "why": why or "未知原因"}
    out.update(extra)
    return out


def _cb_resolved(chain, address, symbol, why, resolved_name=None, resolved_rank=None,
                 resolved_liquidity_usd=None, source=None):
    """解析成功统一形状（和 _cb_fail 对齐）：**用户能一眼看出解析到的是不是他要的那个币**。
    · resolved_name / resolved_chain / resolved_address：解析出来的币名 + 链 + 地址
      （和顶层 chain/address 同值，前端两个都能取；拿不到一律 null）
    · resolved_rank：市值排名（没有就 null）
    · resolved_liquidity_usd：走 GeckoTerminal 池子那条路时的池子流动性（其它路 null）
    · source：这条结果**哪条路**给的（lookup#1 / geckoterminal_search / coingecko platforms /
      dexscreener），绝不合并成一句「已解析」。"""
    return {"ok": True, "chain": chain, "address": address, "symbol": symbol,
            "resolved_name": resolved_name,
            "resolved_chain": chain, "resolved_address": address,
            "resolved_rank": resolved_rank,
            "resolved_liquidity_usd": resolved_liquidity_usd,
            "source": source,
            # 解析成功也带（GMGN 熔断只影响主源，别的源正常时不该因此报错）
            "gmgn_ban_until": _cb_ban_until(),
            "why": why or "未知原因"}


def _cb_progress_text(step, done, total):
    """给前端的进度文案（人话，别只给 step 名）。"""
    label = COSTBASIS_STAGE_TEXT.get(_str(step)) or (_str(step) or "跑着…")
    if step in ("transfers", "retry") and isinstance(done, int) and total:
        return "%s（第 %d/%d 户）" % (label, max(1, done + 1), total)
    return label


def _cb_ds_chain_map():
    """DexScreener chainId → 本工具支持的链（用 costbasis 自己的别名表，避免两处不一致）。"""
    out = {}
    info = getattr(COSTBASIS, "CHAIN_INFO", {}) if COSTBASIS else {}
    alias = getattr(COSTBASIS, "DS_CHAIN_ALIAS", {}) if COSTBASIS else {}
    for chain in info:
        for name in (alias.get(chain) or {chain}):
            out[str(name).lower()] = chain
    return out


def _cb_within_budget(fn, budget, *args, **kw):
    """把一段**不可中断**的阻塞取数硬压在预算里（重试预算再长也拖不住 HTTP 响应）。

    真机事故（2026-09-15）：地址解析走 DexScreener，重试一路拖到 41.66s，前端以为服务死了。
    这里用 daemon 线程 + join(budget)：超预算就**立刻撒手**并返回人话原因，绝不让用户干等
    （线程是 daemon，超时后它自己在后台自然结束，不会拖住进程退出）。
    返回 fn 的返回值；超预算 / 线程炸了 → (None, 人话原因)。
    """
    box = {}

    def run():
        try:
            box["v"] = fn(*args, **kw)
        except Exception as exc:                                # noqa: BLE001 - 取数失败不许炸整块
            box["v"] = (None, "%s: %s" % (type(exc).__name__, exc))

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(max(0.0, float(budget or 0.0)))
    if th.is_alive():
        return None, "超出 %.1f 秒预算（上游重试太慢 → 宁可快失败，不再等）" % float(budget or 0.0)
    return box.get("v", (None, "取数线程没有返回结果"))


def _cb_gmgn_search_addr(addr, chain_hint=None):
    """GMGN market search -q <合约地址>（**1 次请求**）→ 命中且**地址完全一致**才认。

    地址查询比币名查询更严：拿地址去搜，GMGN 可能返回同链的别的币/别的池子 →
    **地址对不上就当没命中**（绝不「搜到什么用什么」，也不拿第 2 条）。
    chain_hint 给了（Solana mint）就只认这条链，且 base58 大小写敏感。
    → ({"chain","address","symbol","name","price_usd","liquidity_usd","mcp","honeypot",
        "n","skipped"}, None) 或 (None, 人话原因)。
    """
    cb = COSTBASIS
    if cb is None:
        return None, "costbasis 模块不可用（GMGN 客户端在那边）"
    try:
        coins, err = cb.gmgn_search(addr)
    except Exception as exc:                                    # noqa: BLE001
        return None, "GMGN search 异常（%s: %s）" % (type(exc).__name__, exc)
    if err:
        return None, "GMGN search：%s" % err
    ordered = cb.order_gmgn_search(coins, addr)
    if not ordered:
        return None, "GMGN search 没搜到「%s」" % addr
    skipped = []
    for coin in ordered:
        chain = cb.chain_of_gmgn(coin.get("chain"))
        got = _str(coin.get("address"))
        if not chain or chain not in _cb_chains() or (chain_hint and chain != chain_hint):
            skipped.append("%s@%s" % (_str(coin.get("symbol")) or "?", coin.get("chain") or "?"))
            continue
        if not _cb_addr_ok(chain, got) or _cb_net_addr(chain, got) != _cb_net_addr(chain, addr):
            skipped.append("%s@%s（地址不是查询的那个）"
                           % (_str(coin.get("symbol")) or "?", chain))
            continue
        return {"chain": chain, "address": _cb_net_addr(chain, got),
                "symbol": _sym(coin.get("symbol")), "name": _str(coin.get("name")),
                "price_usd": _num(coin.get("price")),
                "liquidity_usd": _num(coin.get("liquidity")), "mcp": _num(coin.get("mcp")),
                "honeypot": bool(cb._gmgn_truthy(coin.get("is_honeypot"))),
                "n": len(ordered), "skipped": skipped}, None
    return None, ("GMGN search 命中 %d 条，但没有一条的地址与查询地址完全一致（跳过：%s）"
                  % (len(ordered), "、".join(skipped[:3]) or "无"))


def _cb_addr_why(addr, tried, tail):
    """地址解析失败的 why：**先写结论，再逐条写清试过哪些源、各什么错**（前端要能自证）。"""
    short = addr if len(addr) <= 20 else (addr[:8] + "…" + addr[-6:])
    return ("按合约地址「%s」%s（试过 —— %s）。不猜链：也可以直接给 chain + address 两个参数。"
            % (short, tail, "；".join([t for t in tried if t])))


def _cb_resolve_address(addr, chain_hint=None, deadline=None):
    """合约地址 → 链。**先 GMGN market search（1 次请求）→ DexScreener 兜底（预算硬约束）**。

    chain_hint=None（0x 地址）= 跨链反查；chain_hint="solana"（base58 mint）= 按 solana 过滤
    （base58 大小写敏感，不能跨链乱比）。DexScreener 那条路的判定照样看 pair_chain_id → CHAIN_INFO。
    预算：**整个地址解析压在 RESOLVE_BUDGET 内**，DexScreener 那一步 ≤ RESOLVE_DEX_BUDGET；
    超预算 → ok:false + why 写明试过哪些源、各什么错（正确性不牺牲：宁可快失败也不瞎猜链）。
    """
    if COSTBASIS is None:
        return _cb_fail("costbasis 模块不可用（blood_chips_costbasis.py 读不到）")
    if deadline is None:
        deadline = time.monotonic() + RESOLVE_BUDGET
    tried = []

    # ① GMGN market search -q <地址>：1 次请求就给 chain/address/liquidity（真机验证这条路最快）
    left = deadline - time.monotonic()
    if left > 0:
        hit, gerr = _cb_within_budget(_cb_gmgn_search_addr,
                                      min(RESOLVE_GMGN_BUDGET, left), addr, chain_hint)
        if hit is not None:
            why = ("GMGN market search 命中 %d 条，其中**地址与查询完全一致**的是第 1 条：%s（%s，"
                   "池子流动性 $%s，市值 $%s）%s"
                   % (hit["n"], hit["symbol"] or hit["name"] or "?", hit["chain"],
                      _fmt_liq(hit["liquidity_usd"]), _fmt_liq(hit["mcp"]),
                      "（⚠️ 这条本身是蜜罐，请自行核对！）" if hit["honeypot"]
                      else "，这条不是蜜罐"))
            if hit["skipped"]:
                why += "；跳过 %d 条地址/链对不上的命中（%s）" % (
                    len(hit["skipped"]), "、".join(hit["skipped"][:3]))
            return _cb_resolved(hit["chain"], hit["address"], hit["symbol"], why,
                                resolved_name=hit["name"] or hit["symbol"],
                                resolved_liquidity_usd=hit["liquidity_usd"],
                                source="gmgn search")
        tried.append("GMGN market search：%s" % (gerr or "没命中"))
    else:
        tried.append("GMGN market search：超出 %.0fs 预算，没发请求" % RESOLVE_BUDGET)

    # ② GMGN 没命中 → DexScreener 反查链，但**预算 ≤ RESOLVE_DEX_BUDGET**（重试再慢也拖不住响应）
    left = deadline - time.monotonic()
    if left <= 0:
        tried.append("DexScreener 反查：超出 %.0fs 预算，没发请求" % RESOLVE_BUDGET)
        return _cb_fail(_cb_addr_why(addr, tried, "反查不到链"))
    pool, err = _cb_within_budget(COSTBASIS.dexscreener_main_pool,
                                  min(RESOLVE_DEX_BUDGET, left), chain_hint, addr)
    if pool is None:
        tried.append("DexScreener 反查：%s" % (err or "没取到主池"))
        return _cb_fail(_cb_addr_why(addr, tried, "反查不到链"))
    chain = _cb_ds_chain_map().get((_str(pool.get("pair_chain_id")) or "").lower())
    if not chain or chain not in _cb_chains():
        tried.append("DexScreener 反查到 chainId=%s" % (pool.get("pair_chain_id") or "缺失"))
        return _cb_fail(_cb_addr_why(
            addr, tried, "反查到 chainId=%s，不是本工具支持的链（支持：%s）"
                         % (pool.get("pair_chain_id") or "缺失", "、".join(_cb_chains()))))
    return _cb_resolved(
        chain, addr, _str(pool.get("base_symbol")) or None,
        "合约地址在 DexScreener 反查到主池（chainId=%s，流动性 $%s）；试过 —— %s"
        % (pool.get("pair_chain_id"), pool.get("liquidity_usd"),
           "；".join(tried) or "无"),
        resolved_name=_str(pool.get("base_symbol")) or None,
        resolved_liquidity_usd=_num(pool.get("liquidity_usd")),
        source="dexscreener")


# GeckoTerminal /search/pools 只出「池子」，链和代币都要从**真机核过的字段**里挖：
#   data[].id                       → "<network>_<池地址>"（链名就在前缀里）
#   relationships.base_token.data.id → "<network>_<代币地址>"（去掉前缀 = 代币地址）
def _cb_gt_net_chain(net):
    """GeckoTerminal 的 network → 本工具支持的链。只用**已有表**判定，避免两处不一致：
    costbasis 的 CHAIN_INFO.gecko（eth/polygon_pos/xdai…）→ 面板的 GT_NET_LABELS →
    costbasis 的 CHAIN_ALIAS（eth/matic/sol…）。认不出 / 不在支持名单（如 bsc）→ None，绝不猜。"""
    n = (_str(net) or "").strip().lower()
    if not n:
        return None
    chains = _cb_chains()
    for chain, rec in _dict(getattr(COSTBASIS, "CHAIN_INFO", {})).items():
        if (_str(_dict(rec).get("gecko")) or "").strip().lower() == n:
            return chain if chain in chains else None
    if n in chains:
        return n
    if n in GT_NET_LABELS:
        alias = _str(_dict(getattr(COSTBASIS, "CHAIN_ALIAS", {})).get(n))
        if alias and alias in chains:
            return alias
    return None


def _cb_addr_ok(chain, address):
    """地址形态自检（拿去查链上数据前最后一道）：EVM 必须 0x+40 hex、Solana 必须 base58 32-44。
    形态不对 → False（宁可说解析不出，也不拿一个不像地址的字符串去打上游）。"""
    a = (_str(address) or "").strip()
    if not a:
        return False
    if chain == "solana":
        return bool(_MODE_SOL_RE.match(a))
    return bool(_MODE_EVM_RE.match(a))


def _cb_row_contract(row):
    """面板查询结果的第 1 条**自己**带的「受支持链上的代币合约地址」→ (chain, address, liquidity)。
    只有 kind=token（或地址查询那几路）才认；池子地址（pair_address）**不算代币地址**，绝不顶替。
    读不出来 → None。"""
    r = _dict(row)
    if not r:
        return None
    kind = _str(r.get("kind"))
    if kind is not None and kind != "token":
        return None                                   # pool_hit / lp_pool / quote_token：不是这个币
    chain = _cb_gt_net_chain(r.get("chain_id")) or _str(r.get("chain"))
    chain = _str(chain)
    if not chain or chain not in _cb_chains():
        return None
    addr = _str(r.get("token_address")) or _str(r.get("address"))
    if not _cb_addr_ok(chain, addr):
        return None
    return chain, addr, _num(r.get("liquidity_usd"))


def _fmt_liq(v):
    """流动性/市值这类钱数写给人看（拿不到就写「缺失」，绝不编 0）。"""
    n = _num(v)
    return ("%.6g" % n) if n is not None else "缺失"


def _cb_gmgn_search_top(q, deadline=None):
    """⓪ **GMGN market search（第 1 条路）** → 挑「本工具能分析的」第 1 条。

    排序（见 costbasis.order_gmgn_search）：`is_honeypot=true` **排最后**，再按 liquidity 降序、
    mcp 降序；q 是普通词时符号/名字与 q 完全一致的优先（防仿盘拿到头名）。
    链要能对回本工具的链（sol→solana / eth→ethereum / bsc…），地址形态也要对得上；否则跳过并说明。
    → ({"chain","address","symbol","name","liquidity_usd","mcp","honeypot","n","skipped"}, None)
      或 (None, 人话原因)。"""
    cb = COSTBASIS
    if cb is None:
        return None, "costbasis 模块不可用（GMGN 客户端在那边）"
    try:
        coins, err = cb.gmgn_search(q)
    except Exception as exc:                                    # noqa: BLE001
        return None, "GMGN search 异常（%s: %s）" % (type(exc).__name__, exc)
    if err:
        return None, "GMGN search：%s" % err
    ordered = cb.order_gmgn_search(coins, q)
    if not ordered:
        return None, "GMGN search 没搜到「%s」" % q
    skipped = []
    for coin in ordered:
        chain = cb.chain_of_gmgn(coin.get("chain"))
        addr = _str(coin.get("address"))
        if not chain or chain not in _cb_chains() or not _cb_addr_ok(chain, addr):
            skipped.append("%s@%s" % (_str(coin.get("symbol")) or "?", coin.get("chain") or "?"))
            continue
        return {"chain": chain, "address": addr, "symbol": _sym(coin.get("symbol")),
                "name": _str(coin.get("name")),
                "liquidity_usd": _num(coin.get("liquidity")), "mcp": _num(coin.get("mcp")),
                "honeypot": bool(cb._gmgn_truthy(coin.get("is_honeypot"))),
                "n": len(ordered), "skipped": skipped}, None
    return None, ("GMGN search 命中 %d 条，但没有一条能对回本工具支持的链/地址形态（跳过：%s）"
                  % (len(ordered), "、".join(skipped[:3])))


def _cb_norm_name(v):
    """币名/符号归一（只留字母数字 + 中文，全部小写）：比较「解析到的到底是不是同一个币」用。"""
    s = (_str(v) or "").strip().lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)


def _cb_gt_search_top(q, deadline=None, want_sym=None, want_name=None):
    """② 名称兜底：GeckoTerminal /search/pools?query=<名字> → **按 reserve_in_usd（流动性）降序**
    取第 1 条；链从 data[].id 的 "<network>_" 前缀解析、代币地址取 relationships.base_token.data.id
    （真机验证这条源活着且一次调用就给「币名匹配 + 链 + 代币地址」）。

    ⚠️ 认币保护（防的就是「解析到错币」这个头号 bug）：want_sym/want_name 给了的时候，
    只接受 base 侧符号或名字**和它一致**的池子 —— 一条都对不上就返回 None + 说明
    （而不是硬拿流动性最高那条：查询 eth 时 GT 流动性最高的池子 base 是 USDC，硬拿就等于又分析错币）。
    没给 want（①连币名都没解析出来）才退回「流动性最高那条」，并在返回里标 verified=False
    → 调用方会在 why 里如实写「这条池子没得核对」。
    → ({"chain","address","symbol","liquidity_usd"}, None) 或 (None, 人话原因)。"""
    pools, note = _gt_search_pools(_str(q), False, deadline)[:2]
    if not pools:
        return None, note
    cands = []
    for pool in pools:
        attrs = _dict(_dict(pool).get("attributes"))
        chain = _cb_gt_net_chain(_gt_search_net(pool))
        addr = _gt_rel_token_addr(pool, "base_token")
        if not chain or not _cb_addr_ok(chain, addr):
            continue
        sym, pname = _gt_split_pool_name(attrs.get("name"))
        liq = _num(attrs.get("reserve_in_usd"))
        cands.append({"chain": chain, "address": addr, "symbol": sym, "name": pname,
                      "liquidity_usd": liq})
    if not cands:
        return None, ("命中 %d 条池子，但没有一条能给出受支持链上的 base_token 地址（不猜）"
                      % len(pools))
    cands.sort(key=lambda c: (c["liquidity_usd"] if c["liquidity_usd"] is not None else -1.0),
               reverse=True)
    wsym, wname = _cb_norm_name(want_sym), _cb_norm_name(want_name)
    if wsym or wname:
        for c in cands:
            csym, cname = _cb_norm_name(c["symbol"]), _cb_norm_name(c["name"])
            if (wsym and csym == wsym) or (wname and cname == wname) or (wsym and cname == wsym):
                c["verified"] = True          # base 符号/名字与①解析出来的币一致（交叉核对过）
                return c, None
        top = cands[0]
        return None, ("流动性最高的池子 base 是「%s」，和解析出来的「%s / %s」对不上 → 不敢当"
                      "（换用合约地址查询更准）"
                      % (top["symbol"] or "?", want_name or "?", want_sym or "?"))
    cands[0]["verified"] = False              # ①连币名都没解析出来：这条没得核对，如实标注
    return cands[0], None


def _cb_gecko_id(q, deadline=None):
    """币名/代码 → CoinGecko id：复用面板已有的解析（本地符号索引 → /search），不拉行情、不拉日线。"""
    picked = order_search_coins(_local_index_match(q) or [], q)
    if not picked:
        data, err, _code, _rl = _http_json(URL_GECKO_SEARCH % urllib.parse.quote(q),
                                           deadline=deadline)
        picked = order_search_coins(_list(_dict(data).get("coins")), q)
        if not picked:
            if err:
                return None, "CoinGecko 检索失败（%s），定位不到这个币的 id" % err
            return None, ("CoinGecko 没搜到「%s」——按英文名/代码检索（中文搜不到）；"
                          "也可以直接粘合约地址" % q)
    cid = (_str(picked[0].get("id")) or "").strip()
    if not cid:
        return None, "CoinGecko 检索结果没有 id（拿不到 platforms）"
    return cid, None


def _cb_resolve_name(q, deadline=None):
    """币名/代码 → (受支持链, 合约地址)。**绝不自己另走一条「/search 第一条」的路**。

    2026-09-15 真机事故：这一路原来直接吃 CoinGecko /search 的**第 1 条**（= 模糊搜索的第一条
    = 仿盘/桥接版），10 个查询里 9 个解析到错币（pepe→baby-pepe-5、doge→binance-peg-dogecoin、
    eth→anubis-bridged-eth-anubis、sol→allbridge-bridged-sol…），用户点「算带血筹码」其实在
    分析另一个币。现在按「GMGN market search（主源）→ 面板 /api/lookup 的同一套排序
    （市值排名升序 + 桥接/包装版垫底）→ GeckoTerminal → CoinGecko」逐档回落：

      ⓪ GMGN market search：一次请求拿到「币名 + 链 + 合约地址 + 流动性 + 蜜罐标记」，
         按 liquidity/mcp 降序取第 1 条；is_honeypot=true 排最后并注明（source=gmgn_search）
      ① build_lookup(q) 的第 1 条（**只认第 1 条**，绝不用第 2 条及以后当默认）
         · 这一条自己带「受支持链上的合约地址」→ 直接用（source=lookup#1）
      ② 没有 → GeckoTerminal /search/pools?query=<币名> 按 reserve_in_usd(流动性) 降序取第 1 条，
         链从 data[].id 的 "<network>_" 前缀解析、代币地址取 relationships.base_token.data.id
         （source=geckoterminal_search）
      ③ 仍没有 → CoinGecko /coins/{id} 的 platforms 按优先级选链（source=coingecko platforms）

    ③ 的 id 优先用①那一条的 id（它才是排过序的），①没结果才退回 _cb_gecko_id（本地索引 → /search）。
    三条都空手 → ok:false + 写明**每条路试过什么**（绝不瞎猜链）。
    """
    tried = []
    symbol = name = None
    rank = None

    # ⓪ GMGN market search（**主源**：一次请求就给「币名 + 链 + 合约地址 + 流动性 + 蜜罐标记」）
    pick, gnote = _cb_gmgn_search_top(q, deadline)
    if pick is not None:
        why = ("GMGN market search 命中 %d 条，按 liquidity/mcp 降序取第 1 条：%s（%s，池子流动性 $%s，"
               "市值 $%s）；is_honeypot=true 的已排到最后%s"
               % (pick["n"], pick["symbol"] or pick["name"] or "?",
                  pick["chain"], _fmt_liq(pick["liquidity_usd"]), _fmt_liq(pick["mcp"]),
                  "（⚠️ 这条本身就是蜜罐，请自行核对！）" if pick["honeypot"]
                  else "，本次第 1 条不是蜜罐"))
        if pick["skipped"]:
            why += "；跳过了 %d 条对不回本工具支持的链/地址的命中（%s）" % (
                len(pick["skipped"]), "、".join(pick["skipped"][:3]))
        return _cb_resolved(pick["chain"], pick["address"], pick["symbol"], why,
                            resolved_name=pick["name"] or pick["symbol"],
                            resolved_liquidity_usd=pick["liquidity_usd"],
                            source="gmgn_search")
    tried.append("GMGN market search：%s" % (gnote or "没取到可用的币"))

    # ① 面板既有排序（和 /api/lookup 一字不差的那条链路）
    try:
        payload = build_lookup(q)
    except Exception as exc:                                    # noqa: BLE001
        payload = None
        tried.append("面板查询排序异常（%s: %s）" % (type(exc).__name__, exc))
    rows = _list(_dict(payload).get("results")) if isinstance(payload, dict) else []
    row = _dict(rows[0]) if rows else {}
    cid = (_str(row.get("id")) or "").strip()
    symbol = _sym(row.get("symbol"))          # 面板结果行本来就是大写符号（_sym 口径一致）
    name = _str(row.get("name")) or symbol
    rank = _int(row.get("market_cap_rank"))

    hit = _cb_row_contract(row)
    if hit is not None:
        chain, addr, liq = hit
        return _cb_resolved(
            chain, addr, symbol,
            "「%s」按面板查询排序取第 1 条：%s（%s）；这一条自己带着 %s 上的合约地址"
            % (q, name or "?", "#%s" % rank if rank is not None else "无市值排名", chain),
            resolved_name=name, resolved_rank=rank, resolved_liquidity_usd=liq,
            source="lookup#1")
    place = "#%s" % rank if rank is not None else "无市值排名"
    if rows:
        tried.append("面板查询排序第 1 条是「%s」（%s），但它在受支持链上没有合约地址"
                     % (name or "空", place))
    else:
        tried.append("面板查询排序（CoinGecko / CoinPaprika / GeckoTerminal / 本地索引）"
                     "没搜到「%s」" % q)

    # ② GeckoTerminal /search/pools：一次调用拿到「币名匹配 + 链 + 代币地址」，按流动性降序第 1 条
    pick, note = _cb_gt_search_top(name or q, deadline, symbol, name)
    if pick is not None:
        check = ("base 符号与解析出来的「%s」一致（已交叉核对）" % (symbol or name)
                 if pick.get("verified") else
                 "①没解析出币名/符号 → 这条池子没得核对，请自己核对一下再信")
        return _cb_resolved(
            pick["chain"], pick["address"], _sym(pick["symbol"]) or symbol,
            "GeckoTerminal /search/pools 按流动性降序取第 1 条：%s（%s，池子流动性 $%s）；%s"
            % (pick["symbol"] or "?", pick["chain"], pick["liquidity_usd"], check),
            resolved_name=name, resolved_rank=rank,
            resolved_liquidity_usd=pick["liquidity_usd"],
            source="geckoterminal_search")
    tried.append("GeckoTerminal /search/pools：%s" % (note or "没取到可用的池子"))

    # ③ CoinGecko /coins/{id} 的 platforms（最后一档：id 来自①，①空手才现搜一次）
    if not cid:
        cid, why = _cb_gecko_id(q, deadline)
        if not cid:
            tried.append("CoinGecko 检索：%s" % why)
    if cid:
        chain, token, sym, why = _cb_gecko_platforms(cid, deadline)
        if chain:
            return _cb_resolved(chain, token, sym or symbol,
                                "「%s」→ %s" % (name or q, why),
                                resolved_name=name, resolved_rank=rank,
                                source="coingecko platforms")
        tried.append("CoinGecko platforms：%s" % why)

    return _cb_fail(
        "解析不出「%s」在受支持链（%s）上的合约地址 —— 试过：%s。"
        "换用合约地址查询更准。" % (q, "、".join(_cb_chains()), "；".join(tried)),
        resolved_name=name, resolved_rank=rank)


def _cb_gecko_platforms(cid, deadline=None):
    """CoinGecko /coins/{id} 的 platforms → 按优先级选受支持链。→ (chain, address, symbol, why)。
    选不到一律 (None, None, None, 人话原因)（**绝不瞎猜链**）。"""
    data, err, _code, _rl = _http_json(URL_GECKO_COIN % urllib.parse.quote(cid),
                                       deadline=deadline)
    if not isinstance(data, dict):
        return None, None, None, ("CoinGecko /coins/%s 拿不到 platforms（%s）"
                                  % (cid, err or "无数据"))
    plats = data.get("platforms") if isinstance(data.get("platforms"), dict) else {}
    by_chain = {}
    for plat, addr in plats.items():
        chain = GECKO_PLATFORM_CHAIN.get((_str(plat) or "").lower())
        token = (_str(addr) or "").strip()
        if chain and token and chain not in by_chain:            # 空串 = 这条链上没有合约
            by_chain[chain] = (plat, token)
    supported = _cb_chains()
    for chain in COSTBASIS_CHAIN_PRIORITY:
        if chain in by_chain and chain in supported:
            plat, token = by_chain[chain]
            return chain, token, (_str(data.get("symbol")) or None), \
                "CoinGecko %s 的 platforms 里按优先级选了 %s" % (cid, plat)
    return None, None, None, \
        ("该币在 CoinGecko 没有受支持的链上合约（%s 的 platforms 里没有 %s 里的任何一条）"
         % (cid, "、".join(COSTBASIS_CHAIN_PRIORITY)))


def costbasis_resolve(q):
    """GET /api/costbasis/resolve：q → {ok, chain, address, symbol, why}。

    **地址**（0x / base58）：先 GMGN market search（1 次请求）→ DexScreener 兜底，
    全程压在 RESOLVE_BUDGET 内（超预算就明确 ok:false + 写明试过谁，宁可快失败）；
    **币名/代码**：照旧用 COSTBASIS_BUDGET（要走 build_lookup → GT pools → CoinGecko platforms）。
    """
    raw = (_str(q) or "").strip()
    if not raw:
        return _cb_fail("缺少查询内容 q")
    if _MODE_EVM_RE.match(raw):          # 0x 合约地址 → 先 GMGN search，再 DexScreener 跨链反查
        return _cb_resolve_address(raw, deadline=time.monotonic() + RESOLVE_BUDGET)
    if _MODE_SOL_RE.match(raw):
        # Solana mint：base58 大小写敏感 → 明确按 solana 过滤反查；真查不到再当币名试一次。
        # 两次都在**同一个 12 秒 deadline** 内（防「恰好是 32-44 位 base58 形态的长单词」被误判成地址）
        deadline = time.monotonic() + RESOLVE_BUDGET
        by_addr = _cb_resolve_address(raw, "solana", deadline)
        if by_addr.get("ok"):
            return by_addr
        # 当币名再试一次也**受同一个 12 秒 deadline**（超预算就保留地址那条的原因：宁可快失败）
        left = deadline - time.monotonic()
        if left <= 0:
            return by_addr
        by_name = _cb_within_budget(_cb_resolve_name, left, raw, deadline)
        if isinstance(by_name, dict) and by_name.get("ok"):
            return by_name
        return by_addr
    return _cb_resolve_name(raw, time.monotonic() + COSTBASIS_BUDGET)


def _cb_prune_locked(now=None):
    """内存里的任务/缓存只留有限几条（⛔ 不涉及任何文件）。调用方必须持 _COSTBASIS_LOCK。"""
    now = time.time() if now is None else now
    for jid, rec in list(_COSTBASIS_CACHE.items()):
        if now - rec.get("ts", 0.0) >= COSTBASIS_TTL:
            _COSTBASIS_CACHE.pop(jid, None)
    done = sorted([j for j in _COSTBASIS_JOBS.values() if j["state"] not in ("queued", "running")],
                  key=lambda j: j.get("finished_ts") or 0.0)
    for j in (done[:-COSTBASIS_JOB_KEEP] if len(done) > COSTBASIS_JOB_KEEP else []):
        _COSTBASIS_JOBS.pop(j["id"], None)


def _cb_reply(jid, cached=False):
    """任务快照（status / start 共用一套字段）。"""
    ban_until = _cb_ban_until()
    with _COSTBASIS_LOCK:
        rec = _COSTBASIS_CACHE.get(jid)
        if cached and rec:
            return {"ok": True, "job_id": jid, "state": "done",
                    "progress": COSTBASIS_STAGE_TEXT["done"], "result": rec.get("result"),
                    "chain": rec.get("chain"), "address": rec.get("address"),
                    "symbol": rec.get("symbol"), "name": rec.get("name"),
                    "source": rec.get("source"), "cached": True, "why": None,
                    "gmgn_ban_until": ban_until,
                    "poll_url": "/api/costbasis/status?job_id=%s" % jid}
        job = _COSTBASIS_JOBS.get(jid)
    if job is None:
        if rec:
            return {"ok": True, "job_id": jid, "state": "done",
                    "progress": COSTBASIS_STAGE_TEXT["done"], "result": rec.get("result"),
                    "chain": rec.get("chain"), "address": rec.get("address"),
                    "symbol": rec.get("symbol"), "name": rec.get("name"),
                    "cached": True, "why": None, "gmgn_ban_until": ban_until,
                    "poll_url": "/api/costbasis/status?job_id=%s" % jid}
        return {"ok": False, "state": "error", "job_id": jid,
                "gmgn_ban_until": ban_until,
                "why": "没有这个任务（完成任务只在内存里留 %.0f 分钟）：%s"
                       % (COSTBASIS_TTL / 60.0, jid)}
    # 对外只报 running|done|error（排队的也算 running：前端照常每 3 秒轮询，进度写「排队中」）
    state = "running" if job["state"] == "queued" else job["state"]
    out = {"ok": state != "error", "job_id": jid, "state": state,
           "job_state": job["state"],
           "progress": job.get("progress") or "跑着…",
           "result": job.get("result"), "why": job.get("why"),
           "chain": job.get("chain"), "address": job.get("address"),
           "symbol": job.get("symbol"), "name": job.get("name"),
           "source": job.get("source"),
           "holders": job.get("holders"),
           "stage": job.get("stage"), "done": job.get("done"), "total": job.get("total"),
           "cached": cached, "queued": len(_COSTBASIS_QUEUE),
           "started_at": job.get("started_at"), "finished_at": job.get("finished_at"),
           # GMGN 熔断中时给解封时刻（前端显示「预计 HH:MM 恢复」）；没熔断 → None
           "gmgn_ban_until": ban_until,
           "poll_url": "/api/costbasis/status?job_id=%s" % jid}
    return out


def _cb_ensure_worker_locked():
    """单 worker 线程：同一进程最多 1 个 costbasis 任务在跑（多的排队）。调用方持锁。"""
    t = _COSTBASIS_WORKER[0]
    if t is not None and t.is_alive():
        return
    t = threading.Thread(target=_cb_worker, name="costbasis-worker", daemon=True)
    _COSTBASIS_WORKER[0] = t
    t.start()


def _cb_worker():
    while True:
        with _COSTBASIS_LOCK:
            if not _COSTBASIS_QUEUE:
                _COSTBASIS_WORKER[0] = None
                return
            jid = _COSTBASIS_QUEUE.pop(0)
            job = _COSTBASIS_JOBS.get(jid)
            if job is None or job["state"] != "queued":
                continue
            job["state"] = "running"
            job["started_at"] = _now()
            job["progress"] = COSTBASIS_STAGE_TEXT["start"]
        _cb_run(job)


def _cb_run(job):
    """真跑分析（在 worker 线程里，串行）。任何异常都变成 state=error + why，绝不炸面板。"""
    if COSTBASIS is None:
        with _COSTBASIS_LOCK:
            job["state"], job["why"] = "error", "costbasis 模块不可用（blood_chips_costbasis.py 读不到）"
            job["finished_ts"], job["finished_at"] = time.time(), _now()
        return

    def progress(step, done, total, label):
        with _COSTBASIS_LOCK:
            job["stage"], job["done"], job["total"] = step, done, total
            job["progress"] = _cb_progress_text(step, done, total)
            job["log_n"] = (job.get("log_n") or 0) + 1

    try:
        res = COSTBASIS.analyze(job["chain"], job["address"], int(job["holders"]),
                                progress=progress, source=job.get("source") or "auto",
                                symbol_hint=job.get("symbol"))
    except Exception as exc:                                    # noqa: BLE001
        with _COSTBASIS_LOCK:
            job["state"], job["why"] = "error", "%s: %s" % (type(exc).__name__, exc)
            job["finished_ts"], job["finished_at"] = time.time(), _now()
        return
    with _COSTBASIS_LOCK:
        job["state"], job["result"] = "done", res
        job["progress"] = COSTBASIS_STAGE_TEXT["done"]
        job["finished_ts"], job["finished_at"] = time.time(), _now()
        _COSTBASIS_CACHE[job["id"]] = {"ts": time.time(), "result": res,
                                       "chain": job["chain"], "address": job["address"],
                                       "symbol": job.get("symbol"), "name": job.get("name"),
                                       "source": job.get("source") or "auto"}
        _cb_prune_locked()


def costbasis_start(body):
    """POST /api/costbasis/start → (status_code, payload)。同一币复用，不并行开。"""
    src = _dict(body)
    chain = (_str(src.get("chain")) or "").strip().lower()
    addr = (_str(src.get("address") or src.get("token")) or "").strip()
    symbol = (_str(src.get("symbol")) or "").strip() or None
    name = (_str(src.get("name")) or "").strip() or None
    q = (_str(src.get("q")) or "").strip()
    source = (_str(src.get("source")) or "auto").strip().lower()
    if source not in ("auto", "gmgn", "onchain"):
        return 400, {"ok": False, "why": "source 只支持 auto / gmgn / onchain（收到 %r）"
                                        % (src.get("source"),)}
    if not chain and not addr and not q:
        return 400, {"ok": False, "why": "缺少 q（或者 chain + address 一起给）"}
    if chain or addr:
        if not chain or not addr:
            return 400, {"ok": False, "why": "chain 和 address 要一起给（或者只给 q）"}
        if chain not in _cb_chains():
            return 400, {"ok": False, "why": "chain 不支持：%s（支持：%s）"
                                             % (chain, "、".join(_cb_chains()))}
        resolved = {"ok": True, "chain": chain, "address": addr, "symbol": symbol,
                    "resolved_name": name, "resolved_chain": chain, "resolved_address": addr,
                    "why": "用户指定 chain + address"}
    else:
        resolved = costbasis_resolve(src.get("q"))
        if not resolved.get("ok"):
            # 解析不出链就**不许猜**：把原因原样回给前端（红字展示）
            return 200, _cb_fail(resolved.get("why") or "解析不出链和合约地址")
        name = name or _str(resolved.get("resolved_name")) or None
    chain, addr = resolved["chain"], resolved["address"]
    symbol = symbol or _str(resolved.get("symbol")) or None
    name = name or symbol
    holders = _int(src.get("holders"))
    holders = COSTBASIS_HOLDERS if holders is None else max(1, min(200, holders))
    jid = _cb_job_id(chain, addr)
    now = time.time()
    with _COSTBASIS_LOCK:
        _cb_prune_locked(now)
        rec = _COSTBASIS_CACHE.get(jid)
        if (rec is not None and now - rec.get("ts", 0.0) < COSTBASIS_TTL
                and (rec.get("source") or "auto") == source):
            return 200, _cb_reply(jid, cached=True)              # 已算过（同 source）→ 直接给结果
        job = _COSTBASIS_JOBS.get(jid)
        if (job is not None and job["state"] in ("queued", "running")
                and (job.get("source") or "auto") == source):
            return 200, _cb_reply(jid)                           # 同一个币在跑 → 复用，不并行开
        job = {"id": jid, "state": "queued", "chain": chain,
               "address": _cb_net_addr(chain, addr), "symbol": symbol,
               "name": name, "source": source,
               "holders": holders, "progress": "排队中…", "result": None, "why": None,
               "stage": "start", "done": 0, "total": 1,
               "started_at": None, "finished_at": None, "finished_ts": None}
        _COSTBASIS_JOBS[jid] = job
        _COSTBASIS_QUEUE.append(jid)
        out = _cb_reply(jid)
        _cb_ensure_worker_locked()
    return 200, out


def costbasis_status(job_id):
    """GET /api/costbasis/status?job_id= → (status_code, payload)。"""
    jid = (_str(job_id) or "").strip()
    if not jid:
        return 400, {"ok": False, "state": "error", "gmgn_ban_until": _cb_ban_until(),
                     "why": "缺少 job_id（先用 /api/costbasis/start）"}
    with _COSTBASIS_LOCK:
        _cb_prune_locked()
        if jid in _COSTBASIS_JOBS:
            return 200, _cb_reply(jid)
        rec = _COSTBASIS_CACHE.get(jid)
        if rec is not None and time.time() - rec.get("ts", 0.0) < COSTBASIS_TTL:
            return 200, _cb_reply(jid, cached=True)
    return 404, {"ok": False, "state": "error", "gmgn_ban_until": _cb_ban_until(),
                 "why": "没有这个任务（可能已过期：完成任务只在内存里留 %.0f 分钟）：%s"
                        % (COSTBASIS_TTL / 60.0, jid)}


# ---- 写观察池（读 → 改 → 原子写；写前备份；锁内完成，避免并发丢写） ----

WATCHLIST_BAK = WATCHLIST_JSON + ".bak"
WATCH_ENTRY_FIELDS = ("thesis", "why_i_want_it", "buy_zone", "max_position")

DEFAULT_THRESHOLDS = {
    "crypto_fng_extreme_fear": 25,
    "us_fng_extreme_fear": 25,
    "crypto_drawdown_pct": 50,
    "us_drawdown_pct": 30,
    "crypto_7d_drop_pct": 15,
    "us_7d_drop_pct": 8,
}

_WATCH_LOCK = threading.Lock()


def _watchlist_default():
    """文件不存在时按 monitor 的默认结构建壳（空列表，不替用户塞示例条目）。"""
    return {"thresholds": dict(DEFAULT_THRESHOLDS), "crypto": [], "us": []}


def _watchlist_read():
    """读成可改写的 dict。文件缺失 / 解析不了 → 默认结构；能读出 dict 就保留原样。"""
    obj, ok = _load(WATCHLIST_JSON)
    if not ok or not isinstance(obj, dict):
        return _watchlist_default()
    if not isinstance(obj.get("crypto"), list):
        obj["crypto"] = []
    return obj


def _atomic_write_json(path, obj):
    """写同目录临时文件 → os.replace，避免留下半截 JSON。"""
    folder = os.path.dirname(path) or "."
    tmp = os.path.join(folder, ".%s.tmp.%d" % (os.path.basename(path), os.getpid()))
    try:
        os.makedirs(folder, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise


def _watchlist_write(obj):
    """写前备份 → 原子写。"""
    if os.path.exists(WATCHLIST_JSON):
        shutil.copy2(WATCHLIST_JSON, WATCHLIST_BAK)
    _atomic_write_json(WATCHLIST_JSON, obj)


def watchlist_add(cid, symbol):
    """返回 (crypto 列表, err)。按 id 去重，已存在就不动文件。"""
    cid = (cid or "").strip()
    if not cid:
        return None, "缺少 id"
    with _WATCH_LOCK:
        obj = _watchlist_read()
        rows = obj["crypto"]
        for w in rows:
            wid = _str(_dict(w).get("id"))
            if wid and wid.strip() == cid:
                return rows, None                       # 已存在，不重复加
        entry = {"id": cid, "symbol": _sym(symbol) or "", "market": "crypto"}
        for k in WATCH_ENTRY_FIELDS:
            entry[k] = ""
        rows.append(entry)
        _watchlist_write(obj)
        return rows, None


def watchlist_remove(cid):
    """返回 (crypto 列表, err)。不在列表里就原样返回，不动文件。"""
    cid = (cid or "").strip()
    if not cid:
        return None, "缺少 id"
    with _WATCH_LOCK:
        obj = _watchlist_read()
        rows = obj["crypto"]
        keep = [w for w in rows if (_str(_dict(w).get("id")) or "").strip() != cid]
        if len(keep) != len(rows):
            obj["crypto"] = keep
            _watchlist_write(obj)
        return keep, None


# ------------------------------------------------------------------ /api/data

def _breadth(obj):
    b = _dict(_dict(obj).get("breadth"))
    with_dd = _int(b.get("with_drawdown"))
    if with_dd is None:
        with_dd = _int(b.get("screened"))
    return {
        "screened": _int(b.get("screened")),
        "breadth_drawdown_50pct": _ratio(b.get("breadth_drawdown_50pct")),
        "breadth_ret1y_neg": _ratio(b.get("breadth_ret1y_neg")),
        "mcap_weighted_drawdown_pct": _num(b.get("mcap_weighted_drawdown_pct")),
        "median_drawdown_pct": _num(b.get("median_drawdown_pct")),
        "median_ret1y_pct": _num(b.get("median_ret1y_pct")),
        "drawdown_50pct_count": _int(b.get("drawdown_50pct_count")),
        "with_drawdown": with_dd,
        "with_ret1y": _int(b.get("with_ret1y")),
    }


def _candidates(obj):
    c = _dict(obj)
    counts = _dict(c.get("counts"))
    rows = []
    for r in _list(c.get("candidates")):
        if not isinstance(r, dict):
            continue
        rows.append({
            "symbol": _str(r.get("symbol")),
            "screen_score": _num(r.get("screen_score")),
            "price_usd": _num(r.get("price_usd")),
            "drawdown_from_ath_pct": _num(r.get("drawdown_from_ath_pct")),
            "ret_7d_pct": _num(r.get("ret_7d_pct")),
            "ret_30d_pct": _num(r.get("ret_30d_pct")),
            "ret_1y_pct": _num(r.get("ret_1y_pct")),
            "mcap_usd": _num(r.get("mcap_usd")),
            "vol24h_usd": _num(r.get("vol24h_usd")),
            "tvl_usd": _num(r.get("tvl_usd")),
            "tvl_mcap_ratio": _num(r.get("tvl_mcap_ratio")),
            "category": _str(r.get("category")),
            "also_down_1y": bool(r.get("also_down_1y")) if isinstance(r.get("also_down_1y"), bool) else None,
            "name": _str(r.get("name")),
        })
    return {
        "ts": _str(c.get("ts")) or _str(c.get("ts_iso")),
        "screened": _int(counts.get("screened")),
        "passed": _int(counts.get("passed")),
        "listed": _int(counts.get("listed")),
        "rows": rows,
    }


def _sentiment(obj):
    m = _dict(_dict(obj).get("market"))
    prev_round = _dict(_dict(obj).get("previous_round"))
    out = {"crypto_fng": None}

    c = _dict(m.get("crypto"))
    value = _num(c.get("current"))
    collected = bool(c.get("collected")) or c.get("ok") is True
    if not (collected and value is not None):
        return out

    change = _num(c.get("change_vs_prev"))
    prev = _num(prev_round.get("crypto_fng"))
    if prev is None and change is not None:
        prev = value - change  # 只在文件里没记上轮值时才回推

    out["crypto_fng"] = {
        "value": value,
        "label": _str(c.get("label")) or _str(c.get("rating")),
        "percentile": _ratio(c.get("percentile_30")),
        "prev": prev,
        "change": change,
        "as_of": _str(c.get("value_time")) or _str(c.get("fetched_at")),
    }
    return out


def _triggered_keys(obj):
    """从 triggered 列表里抽出被触发的资产 key（market 级触发不参与资产匹配）。"""
    keys = set()
    for t in _list(_dict(obj).get("triggered")):
        if not isinstance(t, dict):
            continue
        if _str(t.get("kind")) == "market_fng":
            continue
        if _str(t.get("key")) and _str(t.get("key")).endswith("_fng"):
            continue
        for k in (t.get("key"), t.get("label"), t.get("symbol")):
            s = _str(k)
            if s:
                keys.add(s.lower())
    return keys


def _quote(rows, key, key_field):
    for r in _list(rows):
        if isinstance(r, dict) and _str(r.get(key_field)) == key:
            return r
    return {}


def _watchlist(watch_obj, data_obj):
    """用户池 + 行情。crypto 用 id 匹配，us 用 ticker 匹配。"""
    watch = _dict(watch_obj)
    data = _dict(data_obj)
    trig = _triggered_keys(data)
    crypto_rows = _list(data.get("crypto"))
    us_rows = _list(data.get("us"))
    out = []

    for w in _list(watch.get("crypto")):
        if not isinstance(w, dict):
            continue
        wid = _str(w.get("id")) or ""
        q = _quote(crypto_rows, wid, "id") or _quote(crypto_rows, wid, "symbol")
        sym = _str(w.get("symbol")) or _str(q.get("symbol"))
        out.append({
            "symbol": sym,
            "market": "crypto",
            "current_price": _num(q.get("price_usd")),
            "drawdown_from_ath_pct": _neg(q.get("drawdown_from_ath_pct")),
            "ret_7d_pct": _num(q.get("change_7d_pct")),
            "ret_30d_pct": _num(q.get("change_30d_pct")),
            "triggered": bool(wid and wid.lower() in trig) or bool(sym and sym.lower() in trig),
            "thesis": _str(w.get("thesis")) or _str(w.get("why_i_want_it")),
        })

    for w in _list(watch.get("us")):
        if not isinstance(w, dict):
            continue
        ticker = _str(w.get("ticker")) or _str(w.get("symbol")) or ""
        q = _quote(us_rows, ticker, "ticker") or _quote(us_rows, ticker, "symbol")
        dd = q.get("drawdown_from_52w_high_pct")
        if dd is None:
            dd = q.get("drawdown_from_ath_pct")
        out.append({
            "symbol": _str(w.get("symbol")) or ticker or None,
            "market": "us",
            "current_price": _num(q.get("price_usd")) if q.get("price_usd") is not None else _num(q.get("price")),
            "drawdown_from_ath_pct": _neg(dd),
            "ret_7d_pct": _num(q.get("change_7d_pct")),
            "ret_30d_pct": _num(q.get("change_30d_pct")),
            "triggered": bool(ticker and ticker.lower() in trig),
            "thesis": _str(w.get("thesis")) or _str(w.get("why_i_want_it")),
        })

    return out


def build_data():
    cand, cand_ok = _load(CANDIDATES_JSON)
    data, data_ok = _load(DATA_JSON)
    watch, watch_ok = _load(WATCHLIST_JSON)

    missing = []
    if not cand_ok:
        missing.append("blood_chips_candidates.json 缺失或无法解析")
    if not data_ok:
        missing.append("blood_chips_data.json 缺失或无法解析")
    if not watch_ok:
        missing.append("blood_chips_watchlist.json 缺失或无法解析")
    for src, name in ((cand, "candidates"), (data, "data"), (watch, "watchlist")):
        for m in _list(_dict(src).get("missing")):
            s = _str(m)
            if s:
                missing.append("%s: %s" % (name, s))

    return {
        "updated_at": _now(),
        "files": {
            "candidates_json": "ok" if cand_ok else "缺失",
            "data_json": "ok" if data_ok else "缺失",
            "watchlist": "ok" if watch_ok else "缺失",
        },
        "breadth": _breadth(cand),
        "candidates": _candidates(cand),
        "sentiment": _sentiment(data),
        "watchlist": _watchlist(watch, data),
        "missing": missing,
    }


# ----------------------------------------- /api/deep · /api/discount

def _ts_epoch_of(obj):
    """从 ts_epoch / ts_iso / ts 里还原时间戳；拿不到返回 None。"""
    d = _dict(obj)
    e = _num(d.get("ts_epoch"))
    if e is not None:
        return float(e)
    for key in ("ts_iso", "ts"):
        s = _str(d.get(key))
        if not s:
            continue
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z",
                    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.datetime.strptime(s, fmt).timestamp()
            except ValueError:
                continue
    return None


def _ts_iso_of(obj):
    s = _str(_dict(obj).get("ts_iso"))
    if s:
        return s
    e = _ts_epoch_of(obj)
    if e is None:
        return None
    return datetime.datetime.fromtimestamp(e).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def _freshness(obj, limit_h):
    """{stale, age_hours, stale_after_hours}；时间戳读不到就当过期（不编时间）。"""
    ts = _ts_epoch_of(obj)
    if ts is None:
        return {"stale": True, "age_hours": None, "stale_after_hours": limit_h}
    age = (time.time() - ts) / 3600.0
    return {"stale": age > limit_h, "age_hours": round(age, 2), "stale_after_hours": limit_h}


def _not_run(script, path):
    return {"ok": False,
            "reason": "还没跑过 %s（%s 不存在或读不了）" % (script, os.path.basename(path)),
            "ts_iso": None, "ts": None, "stale": True,
            "age_hours": None, "stale_after_hours": None}


def _stale_fields(payload, obj, limit_h):
    payload.update(_freshness(obj, limit_h))
    payload["ts_iso"] = _ts_iso_of(obj)
    payload["ts"] = _str(_dict(obj).get("ts"))
    return payload


def _str_list(seq, limit=None):
    out = []
    for x in _list(seq):
        s = _str(x)
        if s:
            out.append(s)
    return out[:limit] if limit else out


def _pair(v):
    d = _dict(v)
    return {"symbol": _str(d.get("symbol")), "name": _str(d.get("name"))}


# ------------------------------------------------------------ /api/deep

def _deep_macro(obj):
    m = _dict(_dict(obj).get("macro"))
    rows = []
    for r in _list(m.get("indexes")):
        if not isinstance(r, dict):
            continue
        rows.append({
            "label": _str(r.get("label")), "symbol": _str(r.get("symbol")),
            "ok": bool(r.get("ok")),
            "last": _num(r.get("last")),
            "day_change_pct": _num(r.get("day_change_pct")),
            "from_high_pct": _num(r.get("from_high_pct")),
            "from_low_pct": _num(r.get("from_low_pct")),
            "range_pos_pct": _num(r.get("range_pos_pct")),
            "high_52w": _num(r.get("high_52w")), "low_52w": _num(r.get("low_52w")),
            "divisor": _num(r.get("divisor")), "source": _str(r.get("source")),
            "error": _str(r.get("error")),
        })
    return {"rows": rows, "ok_count": _int(m.get("ok_count")), "total": _int(m.get("total"))}


def _deep_crypto(obj):
    c = _dict(_dict(obj).get("crypto_market"))
    return {
        "ok": bool(c.get("ok")),
        "source": _str(c.get("source")),
        "total_mcap_usd": _num(c.get("total_mcap_usd")),
        "mcap_change_24h_pct": _num(c.get("mcap_change_24h_pct")),
        "btc_dominance_pct": _num(c.get("btc_dominance_pct")),
        "eth_dominance_pct": _num(c.get("eth_dominance_pct")),
        "note": _str(c.get("note")) or _str(c.get("cg_error")) or _str(c.get("paprika_error")),
    }


def _deep_tech(t):
    t = _dict(t)
    ma = _dict(t.get("ma"))
    dev = _dict(t.get("ma_dev_pct"))
    ret = _dict(t.get("ret"))
    vol = _dict(t.get("volume"))
    year = _dict(t.get("year"))
    vs = _dict(t.get("vs_ma200"))
    above = vs.get("above")
    return {
        "ok": bool(t.get("ok")),
        "not_listed": bool(t.get("not_listed")),
        "error": _str(t.get("error")),
        "price": _num(t.get("price")),
        "ma_arrangement": _str(t.get("ma_arrangement")),
        "ma": {"ma20": _num(ma.get("ma20")), "ma50": _num(ma.get("ma50")),
               "ma100": _num(ma.get("ma100")), "ma200": _num(ma.get("ma200"))},
        "ma_dev_pct": {"ma20": _num(dev.get("ma20")), "ma50": _num(dev.get("ma50")),
                       "ma100": _num(dev.get("ma100")), "ma200": _num(dev.get("ma200"))},
        "rsi14_d": _num(t.get("rsi14_d")), "rsi14_d_state": _str(t.get("rsi14_d_state")),
        "rsi14_w": _num(t.get("rsi14_w")), "rsi14_w_state": _str(t.get("rsi14_w_state")),
        "atr_pct": _num(t.get("atr_pct")),
        "vs_ma200": {"above": above if isinstance(above, bool) else None,
                     "distance_pct": _num(vs.get("distance_pct"))},
        "ret": {"7d": _num(ret.get("7d")), "30d": _num(ret.get("30d")),
                "90d": _num(ret.get("90d")), "365d": _num(ret.get("365d"))},
        "volume": {"avg7": _num(vol.get("avg7")), "avg30": _num(vol.get("avg30")),
                   "change_pct": _num(vol.get("change_pct")),
                   "state": _str(vol.get("state")), "unit": _str(vol.get("unit"))},
        "year": {"high": _num(year.get("high")), "low": _num(year.get("low")),
                 "from_high_pct": _num(year.get("from_high_pct")),
                 "from_low_pct": _num(year.get("from_low_pct")), "bars": _int(year.get("bars"))},
    }


def _deep_fund(f):
    f = _dict(f)
    llama = _dict(f.get("llama"))
    fees = _dict(f.get("fees"))
    fdata = _dict(fees.get("data"))
    return {
        "mcap_usd": _num(f.get("mcap_usd")), "mcap_source": _str(f.get("mcap_source")),
        "price_usd": _num(f.get("price_usd")),
        "tvl": _num(llama.get("tvl")), "category": _str(llama.get("category")),
        "tvl_change_7d_pct": _num(f.get("tvl_change_7d_pct")),
        "tvl_mcap_ratio_pct": _num(f.get("tvl_mcap_ratio_pct")),
        "fee_yield_30d_pct": _num(f.get("fee_yield_30d_pct")),
        "fee_yield_annualized_pct": _num(f.get("fee_yield_annualized_pct")),
        "fees_24h": _num(fdata.get("total24h")), "fees_30d": _num(fdata.get("total30d")),
        "fees_annualized": _num(fdata.get("annualized1y")),
        "match_kind": _str(f.get("match_kind")) or _str(_dict(f.get("match")).get("match_kind")),
        "matched_protocols": _str_list(f.get("matched_protocols")),
        "llama_matched": llama.get("matched") if isinstance(llama.get("matched"), bool) else None,
        "llama_name": _str(llama.get("name")),
        "llama_matched_protocols": _str_list(llama.get("matched_protocols")),
        "llama_rule": _str(llama.get("rule")),
        "fees_ok": fees.get("ok") if isinstance(fees.get("ok"), bool) else None,
        "fees_match_kind": _str(fees.get("match_kind")),
        "fees_rule": _str(fees.get("rule")),
        "fees_error": _str(fees.get("error")),
        "notes": _str_list(f.get("notes")),
    }


def build_deep():
    obj, ok = _load(DEEP_JSON)
    if not ok or not isinstance(obj, dict):
        return _not_run("blood_chips_deep.py", DEEP_JSON)
    d = _dict(obj)
    assets = []
    for a in _list(d.get("assets")):
        if not isinstance(a, dict):
            continue
        assets.append({
            "id": _str(a.get("id")), "symbol": _str(a.get("symbol")), "name": _str(a.get("name")),
            "mcap_usd": _num(a.get("mcap_usd")), "mcap_source": _str(a.get("mcap_source")),
            "price_usd": _num(a.get("price_usd")),
            "in_candidates": a.get("in_candidates") if isinstance(a.get("in_candidates"), bool) else None,
            "in_watchlist": a.get("in_watchlist") if isinstance(a.get("in_watchlist"), bool) else None,
            "technical": _deep_tech(a.get("technical")),
            "fundamentals": _deep_fund(a.get("fundamentals")),
        })
    failures = []
    for x in _list(d.get("failures")):
        if isinstance(x, dict):
            failures.append({"symbol": _str(x.get("symbol")), "aspect": _str(x.get("aspect")),
                             "reason": _str(x.get("reason"))})
    payload = {
        "ok": True,
        "files": {"deep_json": "ok"},
        "macro": _deep_macro(d),
        "crypto_market": _deep_crypto(d),
        "assets": assets,
        "missing": _str_list(d.get("missing")),
        "failures": failures,
        "universe_info": _dict(d.get("universe_info")),
    }
    return _stale_fields(payload, d, DEEP_STALE_H)


# -------------------------------------------------------- /api/discount

def _dd_stats(s):
    s = _dict(s)
    ub = s.get("unbiased")
    return {
        "count": _int(s.get("count")), "median": _num(s.get("median")),
        "p25": _num(s.get("p25")), "p75": _num(s.get("p75")),
        "min": _num(s.get("min")), "max": _num(s.get("max")),
        "pct_ge_90": _num(s.get("pct_ge_90")),
        "unbiased": ub if isinstance(ub, bool) else None,
    }


def _bucket_common(b):
    b = _dict(b)
    return {
        "label": _str(b.get("label")), "caliber": _str(b.get("caliber")),
        "field": _str(b.get("field")), "band": _str(b.get("band")),
        "suggested_depth_pct": _num(b.get("suggested_depth_pct")),
        "stats": _dd_stats(b.get("stats")),
        "missing": _str(b.get("missing")),
    }


def build_discount():
    obj, ok = _load(DISCOUNT_JSON)
    if not ok or not isinstance(obj, dict):
        return _not_run("blood_chips_discount.py", DISCOUNT_JSON)
    d = _dict(obj)
    cal = _dict(d.get("calibers"))
    buckets = _dict(d.get("buckets"))

    ms = _bucket_common(buckets.get("mainstream"))
    ms_src = _dict(buckets.get("mainstream"))
    ms.update({
        "unbiased": ms_src.get("unbiased") if isinstance(ms_src.get("unbiased"), bool) else None,
        "market_weighted_pct": _num(ms_src.get("market_weighted_pct")),
        "prescreened_stats": _dd_stats(ms_src.get("prescreened_stats")),
        "breadth_sample": {
            "sample_n": _int(_dict(ms_src.get("breadth_sample")).get("sample_n")),
            "screened": _num(_dict(ms_src.get("breadth_sample")).get("screened")),
            "median_pct": _num(_dict(ms_src.get("breadth_sample")).get("median_pct")),
            "drawdown_50pct_count": _num(_dict(ms_src.get("breadth_sample")).get("drawdown_50pct_count")),
        },
        "items": [dict(_pair(r),
                       dd_ath=_num(_dict(r).get("dd_ath")), band=_str(_dict(r).get("band")),
                       price_usd=_num(_dict(r).get("price_usd")),
                       mcap_usd=_num(_dict(r).get("mcap_usd")),
                       vol24h_usd=_num(_dict(r).get("vol24h_usd")),
                       category=_str(_dict(r).get("category")))
                  for r in _list(ms_src.get("items")) if isinstance(r, dict)],
    })

    mm = _bucket_common(buckets.get("meme"))
    mm_src = _dict(buckets.get("meme"))
    mm.update({
        "eligible": _int(mm_src.get("eligible")),
        "excluded_major": _int(mm_src.get("excluded_major")),
        "excluded_breakdown": _dict(mm_src.get("excluded_breakdown")),
        "dedupe": _dict(mm_src.get("dedupe")),
        "insufficient_sample": (mm_src.get("insufficient_sample")
                                if isinstance(mm_src.get("insufficient_sample"), bool) else None),
        "items": [dict(
            symbol=_str(_dict(r).get("symbol")), name=_str(_dict(r).get("name")),
            net=_str(_dict(r).get("net")), price_usd=_num(_dict(r).get("price_usd")),
            dd_180d=_num(_dict(r).get("dd_180d")),
            window_days=_num(_dict(r).get("window_days")),
            days_since_high=_num(_dict(r).get("days_since_high")),
            age_days=_num(_dict(r).get("age_days")),
            liquidity_usd=_num(_dict(r).get("liquidity_usd")),
            fdv_usd=_num(_dict(r).get("fdv_usd")), mcap_usd=_num(_dict(r).get("mcap_usd")),
            vol_h24_usd=_num(_dict(r).get("vol_h24_usd")),
            dd_ath=_num(_dict(r).get("dd_ath")),
            dd_ath_missing=_str(_dict(r).get("dd_ath_missing")),
            ohlcv_missing=_str(_dict(r).get("ohlcv_missing")),
            pool_created_at=_str(_dict(r).get("pool_created_at")),
            candles=_int(_dict(r).get("candles")),
            cg_id=_str(_dict(r).get("cg_id")))
            for r in _list(mm_src.get("items")) if isinstance(r, dict)],
    })

    meta = _dict(d.get("meme_meta"))
    payload = {
        "ok": True,
        "calibers": {
            "mainstream": _str(cal.get("mainstream")),
            "meme": _str(cal.get("meme")),
            "note": _str(cal.get("note")),
            "prescreened_candidates": _str(cal.get("prescreened_candidates")),
            "dd_ath_optional": _str(cal.get("dd_ath_optional")),
            "breadth_p25_p75_note": _str(cal.get("breadth_p25_p75_note")),
        },
        "buckets": {"mainstream": ms, "meme": mm},
        "meme_meta": {"eligible": _int(_dict(meta.get("select")).get("eligible")),
                      "excluded_symbol": _int(_dict(meta.get("select")).get("excluded_symbol")),
                      "nets": _str_list(meta.get("nets")),
                      "ohlcv_missing": _str_list(meta.get("ohlcv_missing")),
                      "cg_missing": _str_list(meta.get("cg_missing"))},
        "sources": _str_list(d.get("sources")),
        "missing": _str_list(d.get("missing")),
        "disclaimer": _str(d.get("disclaimer")),
    }
    return _stale_fields(payload, d, DISCOUNT_STALE_H)


# ----------------------------------------------------------------- /api/brief

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[_-](\d{2})-(\d{2})-(\d{2})")
_RESP_RE = re.compile(r"(?m)^#{1,6}\s*Response\s*$")


def build_brief():
    """最近一次 agent 简报。**带 build**：页面靠它发现自己是不是旧版（代码更新 → 自动刷新）。"""
    try:
        names = [n for n in os.listdir(CRON_DIR) if n.endswith(".md")]
    except Exception:
        return {"ts": None, "brief": "", "build": BUILD}
    if not names:
        return {"ts": None, "brief": "", "build": BUILD}

    def mtime(n):
        try:
            return os.path.getmtime(os.path.join(CRON_DIR, n))
        except Exception:
            return 0.0

    name = max(names, key=mtime)
    ts = None
    m = _TS_RE.match(name)
    if m:
        ts = "%s %s:%s:%s" % (m.group(1), m.group(2), m.group(3), m.group(4))

    try:
        with open(os.path.join(CRON_DIR, name), "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except Exception:
        return {"ts": ts, "brief": "", "build": BUILD}

    hit = _RESP_RE.search(text)
    brief = text[hit.end():].strip() if hit else ""
    return {"ts": ts, "brief": brief, "build": BUILD}


# ----------------------------------------------------------------- 采集状态

_STATE_LOCK = threading.Lock()
_STATE = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "exit_code": None,
    "stage": None,          # {"index","total","label","cmd"}；空转 / 跑完为 None
    "log": [],
}


def _stage_of(index):
    """1 基的段落信息；index 越界返回 None。"""
    if not (1 <= index <= len(REFRESH_STAGES)):
        return None
    label, cmd = REFRESH_STAGES[index - 1]
    return {"index": index, "total": len(REFRESH_STAGES), "label": label, "cmd": " ".join(cmd)}


def _status_snapshot():
    with _STATE_LOCK:
        log = _STATE["log"]
        return {
            "running": bool(_STATE["running"]),
            "started_at": _STATE["started_at"],
            "finished_at": _STATE["finished_at"],
            "exit_code": _STATE["exit_code"],
            "stage": _STATE["stage"],
            "stages": [label for label, _cmd in REFRESH_STAGES],
            "log_tail": "\n".join(log[-LOG_TAIL:]),
        }


def _log_line(line):
    with _STATE_LOCK:
        _STATE["log"].append(line)
        del _STATE["log"][:-LOG_KEEP]


def _refresh_worker():
    codes = []
    _log_line("=== 开始采集 %s ===" % _now())
    env = os.environ.copy()
    env.setdefault("BLOOD_CHIPS_DIR", DATA_DIR)
    for i, (label, cmd) in enumerate(REFRESH_STAGES, start=1):
        _log_line("=== [%d/%d] %s ===" % (i, len(REFRESH_STAGES), label))
        with _STATE_LOCK:
            _STATE["stage"] = _stage_of(i)
        _log_line("$ " + " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd, cwd=DATA_DIR, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=REFRESH_TIMEOUT,
            )
            out = proc.stdout.decode("utf-8", "replace") if isinstance(proc.stdout, bytes) else (proc.stdout or "")
            for line in out.splitlines():
                _log_line(line)
            codes.append(proc.returncode)
            _log_line("[exit %s] %s" % (proc.returncode, " ".join(cmd)))
        except subprocess.TimeoutExpired:
            codes.append(124)
            _log_line("[timeout %ss] %s" % (REFRESH_TIMEOUT, " ".join(cmd)))
        except Exception as exc:
            codes.append(127)
            _log_line("[launch-error] %s: %s" % (" ".join(cmd), exc))

    exit_code = 0
    for code in codes:            # 任一步失败就把失败码暴露出来
        if code != 0:
            exit_code = code
            break

    with _STATE_LOCK:
        _STATE["running"] = False
        _STATE["stage"] = None
        _STATE["exit_code"] = exit_code
        _STATE["finished_at"] = _now()
    _log_line("=== 采集结束 exit=%s %s ===" % (exit_code, _now()))


def start_refresh():
    """返回 False 表示已经在跑。"""
    with _STATE_LOCK:
        if _STATE["running"]:
            return False
        _STATE["running"] = True
        _STATE["started_at"] = _now()
        _STATE["finished_at"] = None
        _STATE["exit_code"] = None
        _STATE["stage"] = None
        _STATE["log"] = []
    t = threading.Thread(target=_refresh_worker, name="blood-chips-refresh", daemon=True)
    t.start()
    return True


# -----------------------------------------------------------------  HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "BloodChipsWeb/1.0"
    protocol_version = "HTTP/1.1"

    # ---- 输出工具 ----
    def _send(self, code, body, ctype):
        if getattr(self, "_sent", False):   # 已经回过一次了，别在同一连接上再写一份
            return
        self._sent = True
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_body(self):
        """把 POST body 读干净（不读干净会污染 keep-alive 上的下一个请求）。"""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            return b""
        try:
            return self.rfile.read(n)
        except Exception:
            return b""

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _error(self, code, msg, extra=None):
        payload = {"error": msg}
        if extra:
            payload.update(extra)
        self._json(code, payload)

    # ---- 路由 ----
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                return self._page()
            if path == "/api/data":
                return self._json(200, build_data())
            if path == "/api/brief":
                return self._json(200, build_brief())
            if path == "/api/status":
                return self._json(200, _status_snapshot())
            if path == "/api/deep":
                return self._json(200, build_deep())
            if path == "/api/discount":
                return self._json(200, build_discount())
            if path == "/api/lookup":
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                payload = build_lookup((qs.get("q") or [""])[0])
                if payload is None:
                    return self._error(400, "缺少查询内容 q")
                return self._json(200, payload)
            if path == "/api/costbasis/resolve":
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                return self._json(200, costbasis_resolve((qs.get("q") or [""])[0]))
            if path == "/api/costbasis/status":
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                jid = (qs.get("job_id") or qs.get("id") or [""])[0]
                code, payload = costbasis_status(jid)
                return self._json(code, payload)
            return self._error(404, "not found: %s" % path)
        except Exception as exc:
            return self._error(500, "internal error: %s" % exc)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        raw = self._read_body()
        try:
            if path == "/api/refresh":
                if not start_refresh():
                    return self._json(409, {"running": True, "error": "refresh already running"})
                snap = _status_snapshot()
                return self._json(200, {"running": True, "started_at": snap["started_at"]})
            if path in ("/api/watchlist/add", "/api/watchlist/remove"):
                return self._watchlist_post(path, raw)
            if path == "/api/costbasis/start":
                return self._costbasis_post(raw)
            return self._error(404, "not found: %s" % path)
        except Exception as exc:
            return self._error(500, "internal error: %s" % exc)

    def _costbasis_post(self, raw):
        """POST /api/costbasis/start：body {q} 或 {chain,address}（+可选 holders）。"""
        body = {}
        if raw:
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception as exc:                    # noqa: BLE001
                return self._json(400, {"ok": False, "why": "body 不是合法 JSON: %s" % exc})
        if not isinstance(body, dict):
            return self._json(400, {"ok": False, "why": "body 必须是 JSON 对象"})
        try:
            code, payload = costbasis_start(body)
        except Exception as exc:                       # noqa: BLE001
            return self._json(500, {"ok": False, "why": "起 costbasis 任务失败: %s" % exc})
        return self._json(code, payload)

    def _watchlist_post(self, path, raw):
        """写观察池：{"ok": true, "watchlist": [...]} / {"ok": false, "error": ...}。"""
        body = {}
        if raw:
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                return self._json(400, {"ok": False, "error": "body 不是合法 JSON: %s" % exc})
        if not isinstance(body, dict):
            return self._json(400, {"ok": False, "error": "body 必须是 JSON 对象"})
        cid = _str(body.get("id"))
        if not cid:
            return self._json(400, {"ok": False, "error": "缺少 id"})
        try:
            if path == "/api/watchlist/add":
                rows, err = watchlist_add(cid, _str(body.get("symbol")))
            else:
                rows, err = watchlist_remove(cid)
        except Exception as exc:
            return self._json(500, {"ok": False, "error": "写观察池失败: %s" % exc})
        if err:
            return self._json(400, {"ok": False, "error": err})
        return self._json(200, {"ok": True, "watchlist": rows})

    def _page(self):
        try:
            with open(HTML_PATH, "r", encoding="utf-8") as fh:
                html = fh.read()
        except Exception as exc:
            return self._error(500, "无法读取 blood_chips_web.html: %s" % exc)
        # 构建号内联进页面（HTML 里写的是占位 __BUILD__）：页面轮询发现自己这份和服务器
        # 给的不一样 → 顶部提示 + 自动刷新，用户不用手动硬刷新。
        html = html.replace("__BUILD__", BUILD)
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def log_message(self, fmt, *args):
        sys.stderr.write("[web] %s %s\n" % (self.address_string(), fmt % args))


def parse_port(argv):
    port = _int(os.environ.get("BLOOD_CHIPS_WEB_PORT"))
    if port is None or not (1 <= port <= 65535):
        port = DEFAULT_PORT
    args = argv[1:]
    for i, a in enumerate(args):
        if a in ("-h", "--help"):
            print("用法: python3 blood_chips_web.py [--port N]")
            print("环境变量: BLOOD_CHIPS_WEB_PORT=%s  BLOOD_CHIPS_DIR=%s"
                  % (DEFAULT_PORT, "脚本所在目录"))
            raise SystemExit(0)
        if a == "--port" and i + 1 < len(args):
            got = _int(args[i + 1])
            if got and 1 <= got <= 65535:
                port = got
        elif a.startswith("--port="):
            got = _int(a.split("=", 1)[1])
            if got and 1 <= got <= 65535:
                port = got
    return int(port)


def main():
    port = parse_port(sys.argv)
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    print("带血筹码面板 → http://127.0.0.1:%d" % port)
    print("数据目录: %s" % DATA_DIR)
    _gecko_list_refresh_async()     # ③ 开机后台拉一次 /coins/list 建内存索引（不占交互预算）
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
