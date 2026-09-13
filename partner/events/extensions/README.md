# Event 扩展清单

文件名使用 `*.event.json`。清单只能给一个新 canonical 名称绑定已经注册的 `target_event`，不能包含 Python 源码或 shell。实例重启后重新构建 Catalog，新定义才生效。

```json
{
  "name": "project.example_probe",
  "version": "1.0.0",
  "series": "project",
  "description": "一个有界项目探针",
  "target_event": "read_file",
  "execution_method": "local",
  "reads_existing_artifact": true,
  "permission_class": "local",
  "idempotent": true
}
```

操作员工作区扩展放在 `partner_workspace/config/event_extensions`；经治理晋升的 Candidate 由 Catalog 读取其执行合同。三种来源均不得绕过 Event allow-list、权限、证据与生产门。
