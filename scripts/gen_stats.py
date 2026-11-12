#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 stats.svg / heatmap.svg（以及浅色主题副本）。

数据全部来自公开信息：
  · GraphQL contributionsCollection —— 近一年的公开贡献图（贡献格 / 贡献总数）
  · REST /users/{login} 与 /users/{login}/repos —— 公开仓库数、各仓库主语言
不触碰任何私有数据，页面上标注的也是这两个来源。

失败处理：所有网络请求先完成，渲染结果先放在内存里；任何一步抛错则
`sys.exit(1)`，**不写文件**，仓库里上一次的 SVG 保持原样。
写入用 tmp + os.replace，避免半截文件。

本地用法：
  GH_TOKEN=xxx python3 scripts/gen_stats.py
  GH_TOKEN=xxx python3 scripts/gen_stats.py --login someone
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")

PALETTES = {
    "deep": dict(
        bg1="#05161B", bg2="#072329", bg3="#0B3037",
        grid="#6FD8C9", grid_op="0.10", accent="#7BE3D2",
        text="#ECF7F4", dim="#86A6AA", glow="#1E8C84", glow_op="0.28",
    ),
    "paper": dict(
        bg1="#FAF5EC", bg2="#F4EDDF", bg3="#E8DFCC",
        grid="#8A8175", grid_op="0.14", accent="#36666A",
        text="#23282A", dim="#7A7266", glow="#FFFFFF", glow_op="0.55",
    ),
}


def gh(path, token=None):
    req = urllib.request.Request(API + path, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "gen-stats",
        **({"Authorization": "Bearer " + token} if token else {}),
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def graphql(query, variables, token):
    data = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(API + "/graphql", data=data, headers={
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "gen-stats",
        "Authorization": "Bearer " + token,
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read().decode())
    if "errors" in d:
        raise RuntimeError(d["errors"])
    return d["data"]


Q = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def fetch(login, token):
    today = dt.date.today()
    start = today - dt.timedelta(days=364)
    data = graphql(Q, {"login": login,
                       "from": start.isoformat() + "T00:00:00Z",
                       "to": today.isoformat() + "T23:59:59Z"}, token)
    user = data.get("user")
    if not user:
        raise RuntimeError("user not found: " + login)
    cal = user["contributionsCollection"]["contributionCalendar"]
    weeks = cal["weeks"]
    days = [d for w in weeks for d in w["contributionDays"]]
    total = cal["totalContributions"]

    profile = gh("/users/" + login, token)
    repos = []
    page = 1
    while True:
        chunk = gh("/users/%s/repos?per_page=100&page=%d&type=owner&sort=updated" % (login, page), token)
        repos.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1

    owned = [r for r in repos if not r.get("fork")]
    return dict(days=days, total=total, weeks=len(weeks),
                public_repos=len(owned), repos=owned)


def metrics(days, total, repos, public_repos):
    counts = [d["contributionCount"] for d in days]
    active = sum(1 for c in counts if c > 0)
    best = cur = 0
    for c in counts:
        cur = cur + 1 if c > 0 else 0
        best = max(best, cur)
    langs = {}
    for r in repos:
        if r.get("language"):
            langs[r["language"]] = langs.get(r["language"], 0) + 1
    top = sorted(langs.items(), key=lambda kv: -kv[1])[:3]
    denom = sum(langs.values()) or 1
    return dict(total=total, active=active, streak=best, repos=public_repos,
                langs=[(n, c, c * 100.0 / denom) for n, c in top],
                max=max(counts + [1]))


def cell_color(p, level):
    """0 = 空格（接近底色），1..4 逐级到主色。"""
    alphas = [0.07, 0.22, 0.42, 0.66, 0.92]
    return '<rect width="11" height="11" rx="2.5" fill="%s" fill-opacity="%.2f"/>' % (
        p["accent"], alphas[level])


def heatmap_svg(data, p):
    days = data["days"]
    m = data["m"]
    # 按周切列
    cols = [days[i:i + 7] for i in range(0, len(days), 7)]
    x0, y0, pitch = 26, 60, 14
    labels, groups = [], []
    last_month = None
    for ci, col in enumerate(cols):
        x = x0 + ci * pitch
        inner = []
        for di, d in enumerate(col):
            c = d["contributionCount"]
            ratio = c / m["max"] if m["max"] else 0
            level = 0 if c == 0 else (1 if ratio <= 0.25 else 2 if ratio <= 0.5 else 3 if ratio <= 0.75 else 4)
            inner.append('<g transform="translate(%d %d)">%s</g>' %
                         (x, y0 + di * pitch, cell_color(p, level)))
        d0 = col[0]["date"]
        month = int(d0[5:7])
        if last_month is not None and month != last_month and int(d0[8:10]) <= 22:
            labels.append('<text x="%d" y="38" font-size="9" fill="%s" opacity="0.7">%d月</text>'
                          % (x, p["dim"], month))
        last_month = month
        # 入场动画：每一列一个延迟，自左向右点亮一次（不循环）
        groups.append('<g class="col" style="animation-delay:%.2fs">%s</g>' % (0.02 * ci, "".join(inner)))

    legend_x = 900 - 26 - 5 * pitch
    ly = 164
    lg = []
    for i in range(5):
        lg.append('<g transform="translate(%d %d)">%s</g>' % (legend_x + i * pitch, ly, cell_color(p, i)))
    lg.append('<text x="%d" y="%d" font-size="9" fill="%s" opacity="0.7" text-anchor="end">少</text>'
              % (legend_x - 8, ly + 9, p["dim"]))
    lg.append('<text x="%d" y="%d" font-size="9" fill="%s" opacity="0.7">多</text>'
              % (legend_x + 5 * pitch + 6, ly + 9, p["dim"]))

    return """<?xml version="1.0" encoding="UTF-8"?>
<!--
  近一年公开贡献热力格（自动生成，请勿手改）
  数据来源：GitHub GraphQL contributionsCollection（公开贡献图）
  空格用近乎底色，高值用主色；入场时自左向右扫一遍点亮，只播一次。
-->
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 186" width="900" height="186"
     role="img" aria-label="近一年公开贡献热力图">
  <title>近一年公开贡献</title>
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0.8" y2="1">
      <stop offset="0" stop-color="{bg1}"/><stop offset="0.6" stop-color="{bg2}"/>
      <stop offset="1" stop-color="{bg3}"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.16" cy="0.1" r="0.8">
      <stop offset="0" stop-color="{glow}" stop-opacity="{glow_op}"/>
      <stop offset="1" stop-color="{glow}" stop-opacity="0"/>
    </radialGradient>
    <pattern id="grid" width="30" height="30" patternUnits="userSpaceOnUse">
      <circle cx="1" cy="1" r="1" fill="{grid}"/>
    </pattern>
    <style>
      .zh {{ font-family: 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, sans-serif; }}
      .mono {{ font-family: ui-monospace, 'SF Mono', 'Menlo', Consolas, monospace; }}
      .col {{ animation: rise .45s ease-out both; }}
      @keyframes rise {{
        from {{ opacity: 0; transform: translateY(4px); }}
        to {{ opacity: 1; transform: translateY(0); }}
      }}
      @media (prefers-reduced-motion: reduce) {{ .col {{ animation: none !important; }} }}
    </style>
  </defs>

  <rect width="900" height="186" rx="14" fill="url(#bg)"/>
  <rect width="900" height="186" rx="14" fill="url(#glow)"/>
  <rect width="900" height="186" rx="14" fill="url(#grid)" opacity="{grid_op}"/>
  <rect x="0.5" y="0.5" width="899" height="185" rx="14" fill="none" stroke="{accent}" stroke-opacity="0.16"/>

  <text class="mono" x="26" y="28" font-size="9.5" letter-spacing="2.2" fill="{dim}">CONTRIBUTIONS · 近一年</text>
  <text class="mono" x="874" y="28" font-size="9.5" letter-spacing="1.2" fill="{dim}" opacity="0.8"
        text-anchor="end">{total} 次贡献 · {active} 天有记录</text>
  {labels}
  {groups}
  {legend}
</svg>
""".format(bg1=p["bg1"], bg2=p["bg2"], bg3=p["bg3"], glow=p["glow"], glow_op=p["glow_op"],
           grid=p["grid"], grid_op=p["grid_op"], accent=p["accent"], dim=p["dim"],
           total=m["total"], active=m["active"],
           labels="\n  ".join(labels), groups="\n  ".join(groups), legend="\n  ".join(lg))


def stats_svg(data, p):
    m = data["m"]
    items = [("近一年贡献", str(m["total"]), "公开贡献图"),
             ("活跃天数", str(m["active"]), "有贡献的天"),
             ("最长连续", str(m["streak"]), "连续天数"),
             ("公开仓库", str(m["repos"]), "不含 fork")]
    blocks = []
    for i, (label, value, note) in enumerate(items):
        x = 36 + i * 156
        blocks.append(
            '<text class="mono" x="%d" y="96" font-size="34" fill="%s">%s</text>'
            '<text class="zh" x="%d" y="120" font-size="11" letter-spacing="0.6" fill="%s">%s</text>'
            '<text class="mono" x="%d" y="136" font-size="8.5" letter-spacing="0.8" fill="%s" opacity="0.55">%s</text>'
            % (x, p["text"], value, x, p["text"], label, x, p["dim"], note))

    bars = []
    maxpct = max([x[2] for x in m["langs"]] + [1])
    for i, (name, cnt, pct) in enumerate(m["langs"]):
        yc = 92 + i * 26
        w = 150 * pct / 100.0
        bars.append(
            '<text class="mono" x="628" y="%d" font-size="10" fill="%s">%s</text>'
            '<rect x="704" y="%d" width="150" height="4" rx="2" fill="%s" fill-opacity="0.14"/>'
            '<rect x="704" y="%d" width="%.1f" height="4" rx="2" fill="%s" fill-opacity="0.85"/>'
            '<text class="mono" x="874" y="%d" font-size="9" fill="%s" text-anchor="end">%d%%</text>'
            % (628, p["text"], name, yc - 2, p["accent"], yc - 2, w, p["accent"], yc + 3, p["dim"], round(pct)))

    return """<?xml version="1.0" encoding="UTF-8"?>
<!--
  公开代码统计（自动生成，请勿手改）
  来源：GitHub 公开贡献图 + 公开仓库列表。只统计公开数据，不含私有仓库。
-->
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 210" width="900" height="210"
     role="img" aria-label="公开代码统计">
  <title>公开代码统计</title>
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0.8" y2="1">
      <stop offset="0" stop-color="{bg1}"/><stop offset="0.6" stop-color="{bg2}"/>
      <stop offset="1" stop-color="{bg3}"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.14" cy="0.08" r="0.85">
      <stop offset="0" stop-color="{glow}" stop-opacity="{glow_op}"/>
      <stop offset="1" stop-color="{glow}" stop-opacity="0"/>
    </radialGradient>
    <pattern id="grid" width="30" height="30" patternUnits="userSpaceOnUse">
      <circle cx="1" cy="1" r="1" fill="{grid}"/>
    </pattern>
    <style>
      .zh {{ font-family: 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, sans-serif; }}
      .mono {{ font-family: ui-monospace, 'SF Mono', 'Menlo', Consolas, monospace; }}
    </style>
  </defs>

  <rect width="900" height="210" rx="14" fill="url(#bg)"/>
  <rect width="900" height="210" rx="14" fill="url(#glow)"/>
  <rect width="900" height="210" rx="14" fill="url(#grid)" opacity="{grid_op}"/>
  <rect x="0.5" y="0.5" width="899" height="209" rx="14" fill="none" stroke="{accent}" stroke-opacity="0.16"/>

  <text class="mono" x="36" y="34" font-size="9.5" letter-spacing="2.2" fill="{dim}">STATS · 公开数据</text>
  <path d="M36 50 H864" stroke="{accent}" stroke-width="0.8" opacity="0.18"/>
  {blocks}
  <path d="M596 66 V150" stroke="{accent}" stroke-width="0.8" opacity="0.16"/>
  <text class="mono" x="628" y="76" font-size="9" letter-spacing="1.6" fill="{dim}" opacity="0.8">主力语言 · 公开仓库</text>
  {bars}
  <text class="mono" x="36" y="190" font-size="8.5" letter-spacing="1" fill="{dim}" opacity="0.6">
    来源：GitHub 公开贡献图与公开仓库列表 · 每日自动更新，不含私有数据
  </text>
</svg>
""".format(bg1=p["bg1"], bg2=p["bg2"], bg3=p["bg3"], glow=p["glow"], glow_op=p["glow_op"],
           grid=p["grid"], grid_op=p["grid_op"], accent=p["accent"], text=p["text"], dim=p["dim"],
           blocks="\n  ".join(blocks), bars="\n  ".join(bars))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", default=os.environ.get("GH_LOGIN", "yabingwang"))
    args = ap.parse_args()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("缺少 GH_TOKEN / GITHUB_TOKEN", file=sys.stderr)
        return 1

    # 先取数：任何异常都在写文件之前抛出，旧图保持不动
    try:
        raw = fetch(args.login, token)
        m = metrics(raw["days"], raw["total"], raw["repos"], raw["public_repos"])
    except Exception as e:  # noqa: BLE001
        print("拉取数据失败，保留上一次的 SVG：%r" % (e,), file=sys.stderr)
        return 1

    data = dict(days=raw["days"], total=raw["total"], m=m)
    outputs = {
        "stats.svg": stats_svg(data, PALETTES["deep"]),
        "stats-light.svg": stats_svg(data, PALETTES["paper"]),
        "heatmap.svg": heatmap_svg(data, PALETTES["deep"]),
        "heatmap-light.svg": heatmap_svg(data, PALETTES["paper"]),
    }
    for name, content in outputs.items():
        tmp = os.path.join(ASSETS, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, os.path.join(ASSETS, name))

    print("贡献 %d · 活跃 %d 天 · 最长连续 %d 天 · 公开仓库 %d"
          % (m["total"], m["active"], m["streak"], m["repos"]))
    print("主力语言：" + ", ".join("%s %d%%" % (n, round(pc)) for n, c, pc in m["langs"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
