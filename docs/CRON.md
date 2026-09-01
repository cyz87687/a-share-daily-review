# 调度模板（CRON / launchd）+ 交易日判断

> 本文件为 **P1 前哨**（pre-P1）内容：定义如何让 `review.py` 在每个 A股交易日收盘后自动运行。
> 所有路径均使用相对路径或环境变量，**禁止硬编码绝对路径**。

---

## 1. 设计原则

- **只在交易日运行**：A股交易日 ≈ 周一至周五，且非法定节假日。
- **收盘后触发**：盘后数据（日线、快照）一般在 15:30 后稳定，建议 16:00 触发。
- **幂等安全**：已运行过的日期会被缓存（`data/cache/{date}/`）与持仓 CSV 覆盖，重跑不会污染。
- **失败即退出**：数据就绪断言（coverage < 0.90 或基准日期不符）会 `sys.exit(1)` 且不产出，调度器应捕获非零退出并告警。
- **密钥外置**：`api_key` 由环境变量 `ADR_LLM_API_KEY` 注入，不在 `config.yaml` 中落盘明文。

---

## 2. 交易日判断伪代码（通用，跨平台可复用）

```python
# src/adr/tradingday.py（建议在 P1 阶段实际落地；此处给出判定契约）
import datetime as dt

CN_HOLIDAYS_2026 = {
    # 格式: "YYYY-MM-DD"，由配置或交易所公告每年刷新
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18",
    "2026-02-19", "2026-02-20", "2026-04-04", "2026-04-05",
    "2026-04-06", "2026-05-01", "2026-05-02", "2026-05-03",
    "2026-05-04", "2026-05-05", "2026-06-19", "2026-09-25",
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
    "2026-10-05", "2026-10-06", "2026-10-07", "2026-12-25",
}


def is_trading_day(date: dt.date, holidays: set[str] = CN_HOLIDAYS_2026) -> bool:
    """返回该日期是否为 A股交易日。"""
    if date.weekday() >= 5:            # 5=Sat, 6=Sun
        return False
    if date.strftime("%Y-%m-%d") in holidays:
        return False
    # 注：此处未校验"交易所实际开市但无集合竞价"等极端情形，
    # 真实生产应接入交易日历 API；缺失时保守跳过。
    return True


def next_trading_day(date: dt.date, holidays: set[str] = CN_HOLIDAYS_2026) -> dt.date:
    cur = date + dt.timedelta(days=1)
    while not is_trading_day(cur, holidays):
        cur += dt.timedelta(days=1)
    return cur
```

> **校验兜底**：即使调度器在交易日触发，运行前 `pipeline` 仍会执行 4 项数据就绪断言；
> 若盘后数据尚未就绪（如 16:00 数据延迟），`assert_data_ready` 抛 `DataUnavailableError` → `sys.exit(1)`，
> 调度器应在数分钟后重试，而非直接告警为故障。

---

## 3. 方案 A：macOS `launchd`（推荐用于本机常驻）

`~/Library/LaunchAgents/com.adr.daily.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.adr.daily</string>

  <key>ProgramArguments</key>
  <array>
    <string>/Users/yzreal/.workbuddy/binaries/python/envs/default/bin/python</string>
    <string>/Users/yzreal/WorkBuddy/2026-08-31-21-09-52/a_share_daily_review/review.py</string>
    <string>run</string>
    <string>--mode</string>
    <string>auto</string>
  </array>

  <!-- 每个工作日 16:00 触发；交易日过滤由脚本内部 is_trading_day 完成 -->
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>16</integer><key>Minute</key><integer>0</integer></dict>
  </array>

  <key>EnvironmentVariables</key>
  <dict>
    <key>ADR_LLM_API_KEY</key>
    <string>__REPLACE_WITH_REAL_KEY__</string>
    <key>TQDM_DISABLE</key>
    <integer>1</integer>
  </dict>

  <key>StandardOutPath</key>
  <string>/Users/yzreal/WorkBuddy/2026-08-31-21-09-52/a_share_daily_review/data/logs/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/yzreal/WorkBuddy/2026-08-31-21-09-52/a_share_daily_review/data/logs/launchd.err.log</string>
  <key>WorkingDirectory</key>
  <string>/Users/yzreal/WorkBuddy/2026-08-31-21-09-52/a_share_daily_review</string>
</dict>
</plist>
```

加载 / 卸载：

```bash
launchctl load  ~/Library/LaunchAgents/com.adr.daily.plist
launchctl unload ~/Library/LaunchAgents/com.adr.daily.plist
# 手动触发一次（不依赖日历）
launchctl start com.adr.daily
```

---

## 4. 方案 B：Linux / 通用 `cron`

```cron
# 每个工作日 16:05 运行；脚本内部再做交易日过滤
5 16 * * 1-5  cd /Users/yzreal/WorkBuddy/2026-08-31-21-09-52/a_share_daily_review && \
  ADR_LLM_API_KEY=__REPLACE_WITH_REAL_KEY__ TQDM_DISABLE=1 \
  /Users/yzreal/.workbuddy/binaries/python/envs/default/bin/python review.py run --mode auto \
  >> data/logs/cron.log 2>&1
```

> `1-5` 仅过滤周末；法定节假日由脚本内的 `is_trading_day` 兜底跳过。

---

## 5. 容器 / CI 替代（GitHub Actions 示例）

```yaml
name: adr-daily
on:
  schedule:
    - cron: "5 8 * * 1-5"   # UTC 08:05 = 北京 16:05
  workflow_dispatch:          # 允许手动触发
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.13" }
      - run: pip install -r requirements.txt
      - run: |
          TQDM_DISABLE=1 ADR_LLM_API_KEY=${{ secrets.ADR_LLM_API_KEY }} \
          python review.py run --mode auto
```

---

## 6. 退出码与告警约定

| 退出码 | 含义 | 调度器动作 |
|--------|------|-----------|
| `0` | 成功（prepare 或 auto 完成） | 无 |
| `1` | 数据未就绪（断言失败，无产出） | 5 分钟后重试一次；仍失败再告警 |
| `2` | 质量门未过（仅告警，产出已生成） | 发送质量告警，不阻断 |
| 非 0 其他 | 运行时异常 | 立即告警 |

> **告警渠道**：P1 阶段可用邮件 / 企业微信机器人；本仓库不内置，由部署方接入。

---

## 7. 首次部署检查清单

- [ ] `requirements.txt` 依赖已装入 venv（PyYAML / Jinja2 / openai）
- [ ] `config.yaml` 中 `llm.api_key` 为空，由 `ADR_LLM_API_KEY` 注入
- [ ] `config/industry_map.yaml` 已按需补充行业映射（缺省走 `TDX#{code}` 兜底）
- [ ] `launchd` / `cron` 工作目录指向项目根
- [ ] 手工跑通一次：`python review.py run --date 2026-08-31 --mode prepare`
- [ ] 确认产出 `output/2026-08-31/{review-*.html,prompt.txt,datapack.json,data/logs/holdings-*.csv}`
- [ ] 启用调度器，观察首个交易日实际产出
