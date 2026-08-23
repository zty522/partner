# 连续运行操作手册

## 启动前

```bash
cd /mnt/e/work/partner
python -m pytest -q
python scripts/partner_control.py status
```

确认没有真实发布、付费、凭证输入等未授权动作，且当前工作区改动已知。

## 创建并后台运行八小时 Campaign

```bash
python scripts/partner_campaign.py start \
  --goal "持续推进五实例项目并验证 Partner 自进化" \
  --duration 8h \
  --instances 01,02,03,04,05 \
  --max-active 2 \
  --report-interval 60m \
  --max-work-items 40 \
  --max-failures 8 \
  --max-retries 2 \
  --max-model-calls 200 \
  --max-cost-units 80 \
  --detach
```

`--detach` 使用 user systemd transient unit；外部 Agent 退出后仍继续。未加 `--detach` 时只创建 Campaign，不自动运行。

## 查看与控制

```bash
python scripts/partner_campaign.py status
python scripts/partner_campaign.py pause --reason "人工检查"
python scripts/partner_campaign.py resume
python scripts/partner_campaign.py stop --reason "用户终止"
```

## 验真

- service active 不是任务完成。
- 查看 WorkItem 的 task_id、状态、产物和 evidence。
- 项目轮必须出现新 IterationReceipt。
- QQ 汇报必须有该任务 step result 的 delivered=true。
- 结束时必须有最终日报；交付失败会明确写入 stop_reason。
