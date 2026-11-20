# china-kol-finder: find Douyin and Xiaohongshu (RedNote) influencers with their official rates

[中文说明](#中文说明)

Planning influencer marketing in China? Give this script a topic, such as `露营` (camping) or `护肤` (skincare),
and a follower range. It searches the two official creator marketplaces, **Xingtu** (星图) for Douyin and **PGY**
(蒲公英) for Xiaohongshu, and writes one CSV shortlist: followers, content categories, median views and
engagements, every quoted rate in yuan, and what 1,000 median views cost at the cheapest rate. The terminal shows
the best value first.

Both marketplaces are built for advertisers with a Chinese business account. This reads the same public creator
listings from anywhere, with no Douyin, Xiaohongshu or advertiser account: the data comes from the
[okdata](https://okdata.online/?src=gh-china-kol-finder) API, $0.004 per page of about 20 Douyin creators and
$0.04 per page of about 20 Xiaohongshu creators. One Python file, standard library only.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/okdataapi/china-kol-finder/blob/main/china_kol_finder.ipynb)
Try it in the browser first: the notebook asks for your key, runs one search and shows the shortlist and a chart.

Good for: brands and agencies sizing a China campaign before they talk to an MCN, cross-border sellers looking
for creators in their category, and analysts comparing what reach costs on the two platforms.

## What you get

The real output of one run on 2026-11-20 (camping, 100k to 1m followers, both platforms, 2 calls, $0.044;
creator names replaced with `dy_creator_a`… for this page; 7.1 yuan per dollar is only an example rate):

```console
$ python3 kolfinder.py --query 露营 --followers 100k-1m --cny-per-usd 7.1 --top 5
Douyin · Xingtu (views = video plays)
  #  creator             followers  median views      rate ¥ (US$)  ¥ / 1k views (US$)  categories
  1  dy_creator_a             302k          939k    15,000 (2,113)        15.98 (2.25)  越野车 / 户外运动
  2  dy_creator_b             947k          2.6m    50,000 (7,042)        19.10 (2.69)  家庭生活记录 / 搞笑剧情
  3  dy_creator_c             224k          138k       3,000 (423)        21.81 (3.07)  户外帐篷 / 露营体验
  4  dy_creator_d             322k          599k    18,800 (2,648)        31.39 (4.42)  露营体验 / 自驾露营
  5  dy_creator_e             174k          151k    12,000 (1,690)       79.25 (11.16)  自驾旅行 / 露营体验
  ...and 15 more in the CSV

Xiaohongshu · PGY (views = note reads)
  #  creator             followers  median views      rate ¥ (US$)  ¥ / 1k views (US$)  categories
  1  xhs_creator_a            208k           16k     8,780 (1,237)      549.88 (77.45)  出行旅游 / 旅行
  2  xhs_creator_b            127k           17k    10,500 (1,479)      627.35 (88.36)  出行旅游 / 旅行
  3  xhs_creator_c            143k           14k     9,000 (1,268)      651.14 (91.71)  汽车 / 用车攻略
  4  xhs_creator_d            101k           15k     9,600 (1,352)      656.63 (92.48)  母婴 / 婴童食品
  5  xhs_creator_e            229k           19k    13,000 (1,831)      682.56 (96.14)  宠物 / 狗
  ...and 15 more in the CSV
```

`shortlists/露营-2026-11-20.csv` has all 40 creators with these columns:

| Column | |
|---|---|
| `platform` | `douyin` or `xiaohongshu` |
| `creator_id`, `name` | The creator on the marketplace |
| `followers` | Followers on Douyin or Xiaohongshu |
| `categories` | The marketplace's content categories for the creator, in Chinese |
| `location`, `gender` | As listed |
| `median_views`, `median_engagements` | Median plays (Douyin) or reads (Xiaohongshu) and engagements of recent posts |
| `engagements_per_view` | `median_engagements / median_views`, on both platforms |
| `engagement_rate_30d` | Xingtu's own 30-day engagement rate (Douyin only) |
| `cheapest_rate`, `cheapest_rate_cny` | The lowest rate the creator is taking orders for, and its type |
| `cny_per_1k_views` | `cheapest_rate_cny / median_views × 1000` |
| `cheapest_rate_usd`, `usd_per_1k_views` | The same in US dollars, when you pass `--cny-per-usd` |
| `rates` | Every quoted rate: integrated or custom video on Douyin, image or video note on Xiaohongshu |
| `marketplace_url` | The creator's page on Xingtu or PGY |

The file starts with a byte-order mark so Excel opens the Chinese text correctly, and text that starts like a
formula (`=`, `+`, `-`, `@`) gets a leading `'`.

## Quick start

1. Get an API key at [okdata.online](https://okdata.online/signup/?src=gh-china-kol-finder). Pay per call, no
   subscription.
2. Run:

```bash
git clone https://github.com/okdataapi/china-kol-finder
cd china-kol-finder
export OKDATA_API_KEY=okd_...     # keep it out of code and commits

# see the plan and the most it can cost first (no key needed)
python3 kolfinder.py --query 露营 --followers 100k-1m --dry-run

python3 kolfinder.py --query 露营 --followers 100k-1m --cny-per-usd 7.1
python3 kolfinder.py --query 护肤 --query 防晒 --platform xiaohongshu --pages 3
```

Search in Chinese: creators describe their content in Chinese, so `护肤` finds far more than `skincare`.

## Flags

| Flag | Default | What it does |
|---|---|---|
| `--query TEXT` | required | What the creators post about. Repeat for more; creators found twice are listed once. |
| `--platform NAME` | both | `douyin` or `xiaohongshu`. Repeat for both. |
| `--followers RANGE` | any | `100k-1m`, `100k-` (at least) or `-500k` (at most). |
| `--by content\|name` | `content` | Match the query against what creators post, or against their names. |
| `--pages N` | `1` | Pages per search, about 20 creators each. |
| `--cny-per-usd RATE` | | Yuan per US dollar, to add US$ columns. Use the day's rate. |
| `--out FILE` | `shortlists/<query>-<date>.csv` | Where to write the CSV. |
| `--top N` | `10` | Rows to print per platform. |
| `--budget USD` | `0.5` | Refuse to start if the run could cost more than this. |
| `--dry-run` | | Print the plan and the most it can cost, then stop. Needs no key. |

Exit codes: `0` done; `1` stopped by an error every call would hit (bad key, low balance) or every search
failed; `2` no key or a bad flag; `3` the plan could cost more than `--budget`, nothing was called; `4` done,
but one of the searches failed.

## Reading the numbers

- **Compare within a platform.** Douyin counts video plays and Xiaohongshu counts note reads, so a Douyin
  creator's cost per 1,000 views is usually far lower. The terminal keeps the two apart for that reason.
- **Rates are list prices** quoted on the marketplace, in yuan, before any negotiation. A rate the creator is not
  taking orders for is kept in `rates` with "(not taking orders)" but never counts as the cheapest.
- **Check the categories.** A content search matches loosely: a travel or food creator who once posted about
  camping can come back for `露营`. Filter on `categories` before you shortlist.
- Medians describe recent everyday posts; a sponsored post can do better or worse.

## Costs

| What | Endpoint | Price |
|---|---|---|
| A page of about 20 Douyin creators | [`douyin.xingtu.creators.search`](https://okdata.online/apis/douyin/xingtu/creators/search/?src=gh-china-kol-finder) | $0.004 |
| A page of about 20 Xiaohongshu creators | [`xiaohongshu.pgy.creators.search`](https://okdata.online/apis/xiaohongshu/pgy/creators/search/?src=gh-china-kol-finder) | $0.04 |

One topic on both platforms, one page each, is $0.044. Before calling anything the script reads the current
prices from the public catalog (no key needed), prints the most the run can cost and refuses runs over `--budget`.
The same key also reads a creator's audience (gender, age, city, interests) and recent posts on both platforms:
see the [Douyin](https://okdata.online/apis/douyin/?src=gh-china-kol-finder) and
[Xiaohongshu](https://okdata.online/apis/xiaohongshu/?src=gh-china-kol-finder) endpoints.

## Use it from an AI client

The same marketplace searches are available to AI assistants through okdata's remote MCP server,
`https://api.okdata.online/mcp`. Ask "find 10 Xiaohongshu skincare creators with 50k to 200k followers, compare
their rates and audiences, and answer in English". Claude Code:

```bash
claude mcp add --transport http okdata https://api.okdata.online/mcp --header 'Authorization: Bearer ${OKDATA_API_KEY}'
```

Cursor, Dify and other clients: see [okdata.online/mcp](https://okdata.online/mcp/?src=gh-china-kol-finder).

## Notes

- A sample project by [okdata](https://okdata.online/?src=gh-china-kol-finder), not affiliated with Douyin,
  Xingtu, Xiaohongshu or PGY. The script only calls the okdata API; bookings still go through the marketplaces.
- Public marketplace listings only. Use them to plan campaigns, not to build profiles of people.
- Tests: `python3 -m unittest discover -s tests` (a local mock server, no network, no cost).
- Issues and ideas are welcome.

---

## 中文说明

找抖音、小红书达人，看报价和性价比。给一个内容方向（比如「露营」「护肤」）和粉丝区间，脚本会在抖音**星图**和小红书**蒲公英**两个官方达人平台上搜索，输出一张 CSV 名单：粉丝数、内容类目、近期作品的播放（阅读）和互动中位数、每种合作形式的报价（元），以及按最便宜报价算出来的「每千次播放成本」。终端里按平台分开，性价比高的排在前面。

不想装环境可以先在浏览器里试：[Colab 笔记本](https://colab.research.google.com/github/okdataapi/china-kol-finder/blob/main/china_kol_finder.ipynb)。不用登录星图或蒲公英，不用抖音、小红书账号。数据来自 [okdata](https://okdata.online/zh/?src=gh-china-kol-finder)：抖音每页约 20 位达人 $0.004，小红书每页约 20 位 $0.04。一个 Python 文件，只用标准库，Python 3.9 以上。

```bash
git clone https://github.com/okdataapi/china-kol-finder
cd china-kol-finder
export OKDATA_API_KEY=okd_...     # 在 https://okdata.online/zh/signup/?src=gh-china-kol-finder 注册拿 Key

python3 kolfinder.py --query 露营 --followers 100k-1m --dry-run      # 先看最多花多少钱，不用 Key
python3 kolfinder.py --query 露营 --followers 100k-1m                # 两个平台各搜一页，$0.044
python3 kolfinder.py --query 护肤 --platform xiaohongshu --pages 3   # 只搜小红书，取三页
```

几点说明：

- **跨平台别直接比**：抖音算的是视频播放，小红书算的是笔记阅读，抖音的千次播放成本通常低得多，所以终端里分平台排序。
- **报价是平台上的刊例价**，单位是元，没算议价。达人暂不接单的报价会留在 `rates` 列里并注明，但不算最便宜的那个。
- **按内容搜会比较宽**：偶尔发过露营内容的旅行、美食博主也会被搜出来，挑人前先看 `categories` 类目。
- 粉丝区间写法：`100k-1m`（10 万到 100 万）、`100k-`（10 万以上）、`-500k`（50 万以下）。
- 同一个 Key 还能查达人的粉丝画像（性别、年龄、城市、兴趣）和近期作品，也能通过 MCP 接进 Claude、Cursor、Dify，直接问「找 10 个 5 万到 20 万粉的小红书护肤博主，对比报价和粉丝画像」，见 [MCP 接入](https://okdata.online/zh/mcp/?src=gh-china-kol-finder)。
- 这是 okdata 的示例项目，和抖音、星图、小红书、蒲公英官方无关；真正下单仍然在星图、蒲公英上完成。只用公开的达人报价信息，请用于投放规划，不要给个人建档。

## License

MIT, see [LICENSE](LICENSE).
