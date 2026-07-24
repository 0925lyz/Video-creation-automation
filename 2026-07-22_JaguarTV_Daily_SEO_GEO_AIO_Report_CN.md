# JaguarTV 每日 SEO / GEO / AIO 日报

日期：2026-07-22（北京时间）

数据日期说明：
- GA4 主站可读到的最近有效日期为 2026-07-20；2026-07-21 在 GA 报告概况页显示为空。
- GSC `sc-domain:jarg.top` 最近可见日期截至 2026-07-20，页面显示 `Last update: 4.5 hours ago`，未覆盖到 2026-07-21。
- B2B 站现在线上存在两个入口：`https://parceiros.jarg.top/` 与 `https://revenda.jarg.top/`。本次 heartbeat 顶部说明优先写的是 `parceiros`，但检查清单仍保留了 `revenda` 路径，两者都已检查。

## 今日结论

1. 主站自然搜索仍处于“有曝光、无点击”的冷启动阶段。GSC 近 3 个月仅看到首页 9 次曝光、0 点击，核心问题不是排名暴跌，而是 SERP 标题/摘要还没有把“今天能看什么 + 如何马上看”说得足够强。
2. 主站内容页结构已经明显优于旧式 IPTV 站：`/jogos-de-hoje` 与 `/baixar-o-app` 已经具备问答式段落、步骤化回答和强 CTA，但 B2B 体系出现了 `parceiros` 与 `revenda` 双站并存、双 WhatsApp、双叙事并行的问题，容易稀释品牌和转化路径。
3. `last30days` 技能已实际执行，但当前 shell 网络环境无法解析外部数据源域名，结果为 0 条证据；今天的竞品观察已改用公开搜索与线上页面结构对照补位，需在后续环境修复后恢复 30 天信号采集。

## GA4 / GSC 昨日数据摘要

### 主站 GA4（最近有效日期：2026-07-20）

- 活跃用户：7
- 新用户：5
- 每位活跃用户平均互动时长：1 分 21 秒
- 事件数：82
- 已见会话来源：
  - `(direct) / (none)`：11 sessions
  - `fb_organic / referral`：3 sessions
  - `l.facebook.com / referral`：1 session
- 已见首次互动来源：
  - `(direct) / (none)`：6 users
  - `l.facebook.com / referral`：1 user
- 热门页面 / 屏幕：
  - `/baixar-o-app` 对应页面标题：12 views / 5 active users / 31 events
  - 首页：6 views / 4 active users / 12 events
  - `Planos JaguarTV com desconto e 30 dias VIP`：6 views / 3 active users / 18 events
  - `Programação de TV hoje e guia de canais`：6 views / 2 active users / 8 events
  - `/jogos-de-hoje`：5 views / 3 active users / 6 events

判断：
- 当前最强的真实行为不是“看比赛页”，而是“安装 / 方案 / 激活”链路，说明站内 CTA 的拉动已经开始生效。
- 流量量级还很小，但 FB referral 已经比 Google organic 更真实，因此主站 SEO 目前仍在起量前期。

### 主站 GSC（最近有效日期：2026-07-20）

- 属性：`sc-domain:jarg.top`
- 时间范围：GSC 页面默认展示近 3 个月
- Total clicks：0
- Total impressions：9
- Average CTR：0%
- Average position：9.7
- 已见查询词：
  - `jaguartv`：0 clicks / 2 impressions
- 已见页面：
  - `https://copa.jarg.top/`：0 clicks / 9 impressions
  - `https://copa.jarg.top/cupom/indicacao`：0 clicks / 1 impression

判断：
- 目前 GSC 可见曝光几乎全部集中在首页，说明 Google 还没有把 `/jogos-de-hoje`、`/baixar-o-app` 等真正承接意图的页面稳定推入结果页。
- 首页已有平均位次 9.7 的信号，但 CTR 仍为 0，属于“高位低点”的早期标题 / 摘要问题。

### B2B 站数据状态

- 本次未读到 `parceiros.jarg.top` 或 `revenda.jarg.top` 的独立 GA4 属性。
- 当前可见 GSC 属性是 `jarg.top` 域属性，但页面级列表里未见 `parceiros` 或 `revenda` 曝光记录。
- 因此今天无法分开给出 B2B 站的后台昨日数据，只能给出页面结构与定位检查。

## 竞品 Last 30 Days 摘要

说明：
- `last30days` 技能可执行，但当前运行环境外网 DNS/源站访问失败，两个主题查询 `IPTV Brasil futebol ao vivo jogos de hoje` 与 `revenda IPTV Brasil` 均返回 0 usable evidence。
- 今天改用公开搜索结果与线上页面模式做替代观察，可视作“last30days 不可达，已用公开搜索替代”。

### 公开搜索替代观察

竞品/邻近内容类型仍主要集中在 4 类：
- “Jogos de hoje / onde assistir” 信息聚合页：用极强的日期词、赛事词、队名词抢短期搜索意图。
- 体育媒体与门户页：强调 `qual canal passa`, `onde assistir`, `horário`, `escalação`, `ao vivo` 等问句。
- IPTV / app 安装页：强调 APK、TV Box、teste grátis、WhatsApp 激活、Downloader code。
- Revenda / parceiro 招募页：强调 margem、desconto、painel、suporte、material、WhatsApp、低门槛启动。

### 我们可借鉴但不照抄的动作

- 把“问题句”放进 H2/H3，而不是只放在 meta title 里。`/jogos-de-hoje` 现在已经在做这件事，后续应继续扩展到更多赛事 / 频道 / 场景页。
- 把 SERP 点击理由前置：不是单纯说“JaguarTV”，而是直接说“hoje”, “onde assistir”, “horários”, “canais”, “TV Box”, “7 dias grátis”。
- B2B 页要尽量用“模型差异 + 收益结构 + 适合谁 + 低库存门槛”快速自解释，`parceiros` 在品牌叙事上更完整，`revenda` 在销售结构上更直接，两者可以融合，但不应长期并行竞争同一类搜索。

## 自动巡检结果

### 主站 `copa.jarg.top`

#### 1. 首页与内容定位

- 首页标题是 `O que assistir hoje: jogos, canais e TV | JaguarTV Hoje`，方向正确，聚焦“今天看什么”。
- `/jogos-de-hoje` 具备明显的 GEO / AIO 友好结构：
  - H1 直接命中 `Jogos de hoje: horários, canais e onde assistir`
  - 有“Hoje / Ontem / Amanhã”切换
  - 有 `Onde assistir aos jogos de futebol de hoje?` 问答段
  - 有 4 步操作说明
  - 每场比赛都有 `Assistir jogo` CTA
- `/baixar-o-app` 同时覆盖 APK、TV Box、Downloader code、WhatsApp 激活、FAQ、视频教程，信息密度高，且很适合承接品牌词与安装词。
- `/copa-do-mundo` 已转为“2026 档案页”，保留了完整结果和路径结构，对长期索引与世界杯历史词仍有价值。

#### 2. CTA 与转化

- 主站核心 CTA 仍然清晰：`Baixar App`、`Ativar pelo WhatsApp`、`Falar no WhatsApp`。
- `/jogos-de-hoje` 的 CTA 设计合理，但所有比赛都统一跳转 `/baixar-o-app`，会把“先看信息”和“立刻激活”之间的意图差异压平。
- `/baixar-o-app` 的 CTA 明确，但仍偏“安装说明页”；如果想提升自然点击后的转化率，可进一步突出“现在就能看什么”和“激活后立刻得到什么”。

#### 3. FAQ / 问答结构

- `/jogos-de-hoje` 已出现典型问题型内容：
  - `Onde assistir jogo hoje?`
  - `Qual canal passa o jogo?`
  - `Como assistir no app?`
- `/baixar-o-app` 已出现安装型 FAQ：
  - `Como baixar o app JaguarTV no celular?`
  - `Como instalar JaguarTV na Smart TV ou TV Box?`
  - `Qual é o código Downloader do JaguarTV?`
- 这些段落很适合继续补充 Schema 和面向 AI Overview 的短答案块。

#### 4. 技术可见性与异常

- `robots.txt` / `sitemap.xml` 通过浏览器访问时被 `ERR_BLOCKED_BY_CLIENT` 拦截，无法在本轮自动确认内容。
- shell 侧公共网络解析也受限，无法用 `curl` 兜底确认 robots / sitemap 状态码。
- 因此今天无法确认 sitemap 是否收录了 `jogos-de-hoje`、`baixar-o-app`、`copa-do-mundo`、`materials`、`tutorials` 等核心 URL。

### B2B 站：`parceiros.jarg.top` 与 `revenda.jarg.top`

#### `parceiros.jarg.top`

- 更像新一代品牌化伙伴站：
  - 明确区分 `Embaixador` 与 `Revendedor`
  - 有强叙事、强信任、强社区语言
  - FAQ 明确回答模式差异、收益与适用人群
- 优势：
  - 更像可被搜索引擎与 AI 系统理解的“官方伙伴计划”
  - 与主站形成“内容消费站 vs 伙伴增长站”的差异更清晰
- 风险：
  - 页脚仍外链 `Site atual de revenda -> https://revenda.jarg.top`
  - WhatsApp 号码与主站不同，体系感较弱

#### `revenda.jarg.top`

- 更像旧版高意图 B2B 转化页：
  - 强调 `50% desconto`
  - 强调低库存起步、积分 / 点卡模型、WhatsApp 成交
  - 强调 `这不是用户页，是代理页`
- 优势：
  - 商业模型讲得更直接，成交阻力低
- 风险：
  - 品牌语言与 `parceiros` 不一致
  - 联系方式、口径、价值叙事与新站并行，容易让 Google、AI 系统和用户都不确定哪一个是“官方主 B2B 页”

### 主站与 B2B 站定位差异

- 主站 `copa.jarg.top`：
  - 目标是抢“今天看什么 / 哪个频道 / 怎么安装 / 足球直播”的流量
  - 更适合 SEO / GEO / AIO 的问题型内容增长
- `parceiros.jarg.top`：
  - 目标是讲清楚“JaguarTV 的伙伴生态”和两种合作模型
  - 更适合品牌型、招募型和结构化 FAQ 型增长
- `revenda.jarg.top`：
  - 目标是更硬的代理成交
  - 适合作为销售着陆页，但不适合作为长期主品牌 B2B 门面继续分流

## 世界杯数据自动化健康检查

- `/copa-do-mundo` 页面可正常打开，且“2026 结果档案页”结构完整。
- 页面中保留了分阶段赛程、结果、淘汰赛路径和 FAQ 化说明，说明历史档案链路仍在工作。
- `/jogos-de-hoje` 页面可见 48 场比赛、分赛事过滤、频道标签与 CTA，说明“今日赛事数据链路”前端可用。
- 目前未验证底层数据源接口、cron、sitemap 收录和埋点回传，只能判断“前台页面可见、内容已渲染、用户可交互”。

健康结论：
- 前台展示层：基本健康
- 数据采集 / 索引提交 / 埋点回传层：今天未完全验证

## 人工补数清单

- 请补充 2026-07-21 的主站 GA4 核心数据：
  - users / sessions / views / events
  - top pages
  - source / medium
- 请补充 2026-07-21 的 GSC 数据（若后台已更新）：
  - clicks / impressions / CTR / average position
  - top queries
  - 高曝光低 CTR query
- 请确认 B2B 独立后台属性：
  - `parceiros.jarg.top` 是否有独立 GA4 / GSC
  - `revenda.jarg.top` 是否仍为正式在用站点
- 请确认：
  - `robots.txt`
  - `sitemap.xml`
  是否在浏览器插件或拦截器之外可正常访问

## 人工填数模板

```md
### 手动补数

- 数据日期：
- 主站 GA4 users：
- 主站 GA4 sessions：
- 主站 GA4 views：
- 主站 GA4 events：
- 主站 GA4 top pages：
- 主站 GA4 top sources：

- 主站 GSC clicks：
- 主站 GSC impressions：
- 主站 GSC CTR：
- 主站 GSC avg position：
- 主站 GSC top queries：
- 主站 GSC high-impression low-CTR queries：

- parceiros GA4 / GSC：
- revenda GA4 / GSC：
```

## SEO / GEO / AIO 机会关键词

- `jogos de hoje onde assistir`
- `qual canal passa o jogo hoje`
- `futebol ao vivo hoje na tv`
- `programação de futebol hoje`
- `baixar app para assistir futebol`
- `apk para tv box futebol ao vivo`
- `código downloader jaguartv`
- `teste grátis tv box futebol`
- `revenda iptv brasil`
- `programa de parceiro tv online`
- `embaixador futebol comunidade`

## 今日建议动作

1. 优先改首页 title / description / hero 文案，让首页更像“今天就能解决观看需求”的入口，而不是泛品牌页。现在首页已经有曝光位次，但 0 CTR，最先该动的是 SERP 点击理由。
2. 尽快确定 B2B 主站到底是 `parceiros` 还是 `revenda`。如果 `parceiros` 是新主站，就应减少旧 `revenda` 对搜索引擎和用户的“官方冲突信号”。
3. 给 `/jogos-de-hoje` 和 `/baixar-o-app` 补强结构化数据与短答案块，继续强化 `onde assistir`、`qual canal passa`、`como instalar` 这类问句的 AIO 适配能力。

## 明日观察点

- GSC 是否在 2026-07-23 更新出 2026-07-21 数据
- 首页 impression 是否从 9 持续增加，还是仍卡在品牌词低量曝光
- `/jogos-de-hoje` 是否开始进入 GSC 页面列表
- `parceiros` 是否开始出现在 GSC 页面维度中
- 旧 `revenda` 与新 `parceiros` 是否继续双轨并行，造成定位冲突
