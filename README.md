# MoonTV 视频源聚合(每日自动更新)

每天自动抓取上游视频接口源,解码、合并去重、实测各站分类接口后按内容分档,产出:

| 文件 | 说明 |
|---|---|
| `output/moontv_完整版.json` | 全部 117 个源(含 cmliu 的电视直播 lives) |
| `output/moontv_人版.json` | 普通源(不含成人站;部分站自带"伦理片/福利"小栏目,清单中有标记) |
| `output/moontv_18禁版.json` | 仅成人源 |
| `output/源清单.xlsx` / `源清单.csv` | 全量清单:成人判定、判定依据、测活状态、内容标签、完整分类名 |

## 上游来源
- `pqguanyinli/moontv_shipinyuan`(仓库1 增强版 94 站 / 仓库2 基础版 20 站 / 仓库3 26 站,Base58 编码)
- `cmliu/cmliu` 的 `tvapi_config_json`(51 站 + 直播,Base58 编码)
- `sp.696.de5.net?format=0&source=full`(69 站,明文)

## 分类规则
1. **实测优先**:逐站请求 `{api}?ac=list` 取分类名,强成人关键词(无码/传媒/麻豆/人妻…)命中 ≥3 条且占比 ≥30% → 判为成人站;
2. 上游明确标记(`AV-` 前缀、🔞、已知成人域名)作为兜底与复核;
3. 正规站自带的单个"伦理片/福利"栏目不算成人站,在清单"含成人栏目"列单独标记,可自行剔除;
4. 顺带测活:探测失败标记"失活"。

## 启用 GitHub 自动推送
1. 在 GitHub 建仓库(建议 **Private** 或将仓库设为 18+),拿到地址,如 `https://github.com/你的用户名/你的仓库.git`
2. 写入两个文件(不要提交):
   ```bash
   echo "https://github.com/你的用户名/你的仓库.git" > /home/tvsource/github_repo
   echo "ghp_你的私人令牌"            > /home/tvsource/github_token   # fine-grained PAT,给 Contents 读写权限
   ```
3. 之后每天定时任务会自动 commit + push;未配置时仅本地提交。

## 加速
- 管线每天自动实测播放链路(取片→m3u8→TS切片前256KB):`output/测速排行.csv` 与 Excel"播放测速"sheet,评级 快≥1MB/s、中≥250KB/s、其余为慢
- **选快源是最有效的加速**:实测普通影视站普遍被站方限速(100-400 KB/s),成人源多为 Cloudflare 托管(7-11 MB/s)
- 约 7 成源走 Cloudflare:可用 [CloudflareSpeedTest](https://github.com/XIU2/CloudflareSpeedTest) 优选 IP 后写入本地 hosts,或自建 CF Worker 反代并缓存 m3u8/TS
- 播放端:MoonTV/LunaTV 开启多源聚合,播放器增大缓冲

## 运行
- 手动:`/home/tvsource/run.sh`
- 定时:每天 05:30(crontab),日志 `update.log`
- 改上游/规则:编辑 `update.py` 顶部的 `SOURCES` 与成人关键词/阈值
