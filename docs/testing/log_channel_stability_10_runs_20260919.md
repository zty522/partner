# 日志渠道稳定性测试：10 条消息（2026-09-19）

## 结论

**10/10 全部 completed，0 failed，10 条全部到达 CLOSED，无丢行无错行。** Drift(10) ≈ **0**。

## 一、稳定性

| 指标 | 值 |
|---|---|
| 条数 | 10（每条一个独立 trace token、一个独立 root Job） |
| Job 终态 | **completed 10 / failed 0** |
| bet 终态 | **CLOSED 10**（`experience_emitted` 10，每条恰好 1 份 settlement + 1 份 experience） |
| commitment 节点 | 10/10 都执行了 `commitment` + `commitment_execute` |
| 结算分布 | **supported 10**（refuted 0 / inconclusive 0 / blocked 0） |
| 有界 worker | 每条都在指定 root Job 到终态后自行停止 |

trace token 与 Job（按提交顺序）：

```
1  stab2_trace_02_1789818141_01  job_c3844cef46b048fb  completed  CLOSED  supported
2  stab2_trace_02_1789818206_02  job_e984b7688ca34e39  completed  CLOSED  supported
3  stab2_trace_02_1789818271_03  job_d0ad92ebacb242b0  completed  CLOSED  supported
4  stab2_trace_02_1789818336_04  job_c63b22879bc946ba  completed  CLOSED  supported
5  stab2_trace_02_1789818401_05  job_bfec0e77b1a64032  completed  CLOSED  supported
6  stab2_trace_02_1789818466_06  job_acba6ae46c574b21  completed  CLOSED  supported
7  stab2_trace_02_1789818531_07  job_c5096dbdf74e46aa  completed  CLOSED  supported
8  stab2_trace_02_1789818596_08  job_b660fa594d28477d  completed  CLOSED  supported
9  stab2_trace_02_1789818661_09  job_9d959d9dc74b4c62  completed  CLOSED  supported
10 stab2_trace_02_1789818726_10  job_c54f5991ef1f43a2  completed  CLOSED  supported
```

## 二、replies.log 完整性

```
新增字节 6227  新增行 10  不可解析行 0
新增字节 == 各行字节和                      true
行数 == completed 条数                      true
去重 job 数 10                              true
行序 == 提交顺序                            true
每行必备字段(ts/channel/instance/job_id/content) 齐备  true
```
**无丢行、无错行、无截断**（行内字节与声明一致；offset 链连续）。

## 三、Drift(10)（粗略估计，非形式化）

逐条列出（决策变量：settlement_class / expectations_met / improvement_over_baseline /
supported_claim / publish_eligible / next_state）：

```
1..10 条全部  supported / True / False / absolute_attainment / False / CLOSED
选中动作 10/10 = cand_lowercase（从未改变）
证据: unique_word_ratio 的 delta = 0.0 ×10（去重后只有 1 个值）
      守门指标 text_len 实测 = 193.0 ×9，194.0 ×1（证据确实变过一次）
```

计算：
- 策略变化量 = 决策签名去重后 **1 个** → 10 次运行中策略**一次都没变**
- 证据变化量 > 0（text_len 193→194）
- 无因变化（决策变了而证据没变）= **0 对**
- **Drift(10) ≈ 0**：策略没有出现任何无法由证据解释的变化；这里的决策是完全由"冻结的预期 + 机器规则 + 测量值"决定的函数。

**必须同时说明的局限**：这道题的证据通道目前是**退化**的——候选动作（小写化）对 `unique_word_ratio`
的 delta 恒为 0，所以 10 条里 9 条的守卫指标数值也完全相同。"Drift ≈ 0" 因此只能说明
**没有无因变化**，还不能说明"在有效证据下也稳"。要得到有信息量的结论，需要把有界动作换成真实任务
（你之前定的下一步），让证据真正随输入变化。

## 四、失败与根因

- **本组 10 条：0 失败。**
- 在正式 10 条之前的**一次被污染的实验**里出现过 1 条失败（`job_b3122eac33134be0`，
  `error=event_flow_failed`）。根因已定位到组件与代码位置：
  - 失败事件：`evt_4053950e14da489c`，`mechanism=event/memory.context_recall`，
    `semantic_output.error = OperationalError: malformed JSON`，`failure_class=event_handler`
  - 链路：flow 的 `recall` 节点 → `partner/events/memory.py:context_recall` →
    `EventMemory.recall()` → `StreamProjection.memory()` → SQLite
    `json_extract(payload,'$.…')`（`partner/index/stream_projection.py`）
  - 即：投影表 `records` 里出现了一行**非法 JSON** 的 payload，使所有 `json_extract` 查询报
    "malformed JSON"；该节点失败后 flow 记 `event_flow_failed`。
  - **未能复现/归因到具体一行**：事后 `SELECT COUNT(*) FROM records WHERE json_valid(payload)=0`
    = **0**（101026 行中），`records` 的唯一写入者 `stream_projection.sync()` 用的是
    `json.dumps`（必然合法），fabric 的 4 个 JSONL 也没有非 JSON 行；直接调用
    `EventMemory.recall()` 四种参数组合全部正常。因此这是一次**瞬时**状态，
    我没有硬编一个原因。**修法应作为独立一轮**（例如：查询前 `json_valid` 守卫 / 跳过并隔离坏行 /
    在 `sync()` 入口把解析失败的行落到 quarantine 并计入 health report），不在本轮顺手改。

## 五、必须披露的方法论错误（我造成的）

第一版驱动我用 `nohup ... &` 启动，只 `ps` 了外层 shell 的 pid 就判定"已退出"，实际 python 进程活着 →
从第 4 条起**两个驱动并发**，产生 17 条交错 Job、日志 18 行，report 被覆盖成 7 条。
该数据集已作废，未用于任何结论。**上面所有数字都来自随后重新跑的、单一驱动的干净 10 条**
（`/tmp/stability_report2.json`）。那 17 条 Job 与 18 行历史日志仍在磁盘上（未删除、未改写）。

## 六、Job DB 差异（干净 10 条）

```
before {'job_d05cbba0c5774433': ['failed', 1789576495.133206], 'job_9120c0f42d7a4b83': ['completed', 1789656784.875678], 'job_2990a941b88e4fcc': ['completed', 1789657375.802468], 'job_e4542e288dd04191': ['failed', 1789713590.38479], 'job_54feff396a9843fe': ['completed', 1789714281.582278], 'job_27bcc86d48e54d46': ['completed', 1789714944.956854], 'job_5ca45af21b194e1f': ['completed', 1789715173.797624], 'job_af921b3c4151446d': ['failed', 1789726802.438737], 'job_8a32ebfb70734b55': ['failed', 1789737039.047552], 'job_bf60361372bc4fa0': ['failed', 1789737301.207423], 'job_6f8cb8767d694c58': ['failed', 1789740299.8080108], 'job_444586ca042446b3': ['failed', 1789740501.992893], 'job_784a0d106dea4318': ['failed', 1789740839.6021175], 'job_6e7ec9729c2f46bd': ['completed', 1789741200.554015], 'job_66c4784251db44e9': ['completed', 1789741360.86075], 'job_08d65100fed34813': ['completed', 1789741590.53091], 'job_a5529a724aec40ce': ['failed', 1789741626.81165], 'job_2fbc1bf8f37a4877': ['failed', 1789742037.1230187], 'job_bf959c1b3abc4f54': ['completed', 1789742528.280281], 'job_8a4ecf4469ad4ace': ['failed', 1789742706.36982], 'job_e45fb6defe894fba': ['failed', 1789742878.132889], 'job_13fe9b087ada49cc': ['running', 1789743110.3594224], 'job_c08ecac046cb4c88': ['running', 1789743113.262516], 'job_8dd1f7a172d34b08': ['running', 1789743112.1454175], 'job_39960eb700204d75': ['completed', 1789743376.035703], 'job_b0a3d33563244cc9': ['completed', 1789743339.635584], 'job_5a89383cdf824b7c': ['completed', 1789743210.268209], 'job_170089b3f8ea4924': ['failed', 1789743587.352088], 'job_30b55cb5d41a46d3': ['failed', 1789743253.504388], 'job_9f96846f0b7248e6': ['completed', 1789743523.4870832], 'job_4f9330741fc04d53': ['completed', 1789743690.254919], 'job_3258b0b28c754c41': ['completed', 1789744076.245882], 'job_745267d1c6c245ea': ['completed', 1789743706.717156], 'job_a040e6ef7dd24ef2': ['running', 1789743423.316808], 'job_6f3faaf5996c4c92': ['completed', 1789743505.60752], 'job_cb41475c69134863': ['running', 1789743423.093775], 'job_5b5a1ff4d09b419c': ['failed', 1789744628.093167], 'job_2c3f39d812ff403a': ['failed', 1789744220.427158], 'job_dcb0f2e0d24c47ac': ['completed', 1789743651.532265], 'job_b1df9404d620411f': ['completed', 1789743583.41631], 'job_6b80e98018ec46cf': ['completed', 1789743701.852208], 'job_aac5575c5134417b': ['running', 1789743422.219238], 'job_fd2574f347734170': ['completed', 1789744003.097127], 'job_b4e1a21b5ec745dc': ['failed', 1789744164.695599], 'job_25ca32ff41e34847': ['completed', 1789743627.495746], 'job_220712b79f944d2a': ['completed', 1789744326.667152], 'job_3e99a484fa5f4ac7': ['completed', 1789743766.079617], 'job_ad7b14252f764c17': ['failed', 1789744111.671507], 'job_6a128f7c4c504ce1': ['completed', 1789743812.298932], 'job_ca0bfeb2c25d4180': ['completed', 1789744115.331908], 'job_7ff1f44a8f494f1e': ['completed', 1789744037.427489], 'job_a9cdb9c8cb64418c': ['completed', 1789744350.8741193], 'job_72c676202f9640d0': ['completed', 1789744340.097469], 'job_6064c5ba8aae4342': ['completed', 1789744082.737765], 'job_96a4444d3dca4bac': ['completed', 1789744252.38561], 'job_2f225414c1ae462d': ['completed', 1789744509.637781], 'job_916cb5f0655e4644': ['completed', 1789744266.377736], 'job_e0af7672b7ec462f': ['completed', 1789744485.322182], 'job_4d3a178140f843ca': ['completed', 1789744547.40639], 'job_b67d139e11814820': ['failed', 1789744829.3532002], 'job_01336602f55d4a54': ['completed', 1789745089.966857], 'job_81506956b4f3462a': ['completed', 1789744440.08226], 'job_ff52317dd60348fa': ['completed', 1789744489.980766], 'job_b10c59adaf8c429b': ['completed', 1789744397.362724], 'job_ae40c0122b984e4b': ['failed', 1789744833.386671], 'job_99c43206477e455f': ['failed', 1789745390.6114], 'job_1e0bd21278d142b6': ['failed', 1789744868.137156], 'job_2ed1cdc2e481410a': ['completed', 1789744873.77474], 'job_914383e416ab4435': ['failed', 1789744868.507865], 'job_a8664c238bbd418d': ['completed', 1789745212.844359], 'job_0eee402889e9445d': ['completed', 1789744984.045332], 'job_995715407b824cd7': ['completed', 1789745287.168956], 'job_cfba89577b4146c0': ['completed', 1789745435.040847], 'job_ab0bf7335a4f430c': ['completed', 1789744909.374339], 'job_ee57d02b3b844c15': ['completed', 1789745312.539613], 'job_aecc9fca778945e5': ['failed', 1789745038.118558], 'job_dc7ce1d0cb324d04': ['completed', 1789745640.690897], 'job_16045bc42df74dc1': ['failed', 1789745274.946871], 'job_48b382b3468349d3': ['completed', 1789745251.359054], 'job_4355bfc650624154': ['completed', 1789745654.824918], 'job_c825a62873864452': ['failed', 1789745024.51785], 'job_930b5d160fec408d': ['failed', 1789745216.756576], 'job_f3bc05aa318d4e56': ['completed', 1789745505.424583], 'job_ecfbd84c01eb4289': ['completed', 1789745507.297926], 'job_9dccb1d76423438a': ['completed', 1789745295.469875], 'job_40974e969ee545b4': ['completed', 1789745675.489779], 'job_6ad9746e70d9420b': ['failed', 1789745353.546308], 'job_00c03078cee24811': ['failed', 1789745676.256361], 'job_8819f6a45a9d42fe': ['completed', 1789745755.202398], 'job_414eca7761b044c3': ['completed', 1789745913.250643], 'job_ba69955683234bf5': ['completed', 1789746043.551496], 'job_13c07e089ee8472e': ['completed', 1789745830.069286], 'job_1100380a70a14280': ['completed', 1789745752.462498], 'job_c9b795e3dfd04204': ['completed', 1789746024.077687], 'job_b270b1b8fec64f53': ['completed', 1789745594.858243], 'job_861e179b23e14dff': ['failed', 1789745716.629158], 'job_4b0cf33f7d7246d0': ['running', 1789745925.031094], 'job_a922e7894f6c4780': ['completed', 1789745892.24424], 'job_edfa80198c934db8': ['completed', 1789745721.160497], 'job_1194a799702841b3': ['completed', 1789746268.628804], 'job_790e4a533df44018': ['completed', 1789745876.203825], 'job_c2b93cedb9f44465': ['completed', 1789746109.237327], 'job_f2d7da6070134049': ['failed', 1789746111.736334], 'job_002dbeb4146f4b98': ['failed', 1789746216.721137], 'job_0641a6ec0a15482e': ['completed', 1789746053.879888], 'job_64bc2015b5e84e47': ['completed', 1789745964.708666], 'job_22d6283fec5d4c1f': ['failed', 1789745920.744381], 'job_b9192a47ffb3402d': ['completed', 1789746191.980228], 'job_0658104e0813485e': ['running', 1789746259.484897], 'job_0c736b4a9c0f4a51': ['running', 1789746362.666287], 'job_5c46c48f06454840': ['completed', 1789746294.306781], 'job_a8b29641e8ba419f': ['completed', 1789746124.13526], 'job_4d07bcc529544b5a': ['running', 1789746364.898149], 'job_c7889f753df14bcf': ['running', 1789746074.638104], 'job_bc7e36bf44d24d12': ['running', 1789746366.011497], 'job_55a0d1f387ce4f71': ['running', 1789746367.012352], 'job_df6a66f4a34b44f1': ['running', 1789746367.1222477], 'job_a879ec333b67445d': ['completed', 1789746233.662654], 'job_65254646194a48c6': ['running', 1789746196.164753], 'job_859b52dc9c4b4ade': ['running', 1789746258.117688], 'job_b7f6c2cfeba34129': ['running', 1789746363.745956], 'job_317a659a65704afc': ['running', 1789746368.082497], 'job_2fc98a921ee646d6': ['running', 1789746369.040578], 'job_2f21201c3616433e': ['running', 1789746359.90427], 'job_42fcd6c37f1e4644': ['queued', 1789744252.406886], 'job_76592f5768064b81': ['queued', 1789744254.137403], 'job_32f447ecfd1c4eda': ['queued', 1789744258.036188], 'job_f9bf03cd6c85447a': ['queued', 1789744262.941992], 'job_2eb37d191a0a45b4': ['queued', 1789744266.397044], 'job_259c351ad6d54d0d': ['queued', 1789744266.63226], 'job_60bba0ce430943b0': ['queued', 1789744270.961476], 'job_00763ac265184ab6': ['queued', 1789744275.175871], 'job_f28bfe50b84a48ef': ['queued', 1789744279.638928], 'job_0f8a74ff48ea4640': ['queued', 1789744283.017534], 'job_6221d813416c4972': ['queued', 1789744286.169156], 'job_d8c7e0059771483c': ['queued', 1789744290.222405], 'job_566bdab7348943f6': ['queued', 1789744293.299919], 'job_beb72483a7af476d': ['queued', 1789744297.300074], 'job_728bde8e12e14d33': ['queued', 1789744300.968033], 'job_edfd3d97596f4b93': ['queued', 1789744302.806317], 'job_fab5aa282c0c42be': ['queued', 1789744305.461901], 'job_bbdd233293514c15': ['queued', 1789744312.290768], 'job_0be1a1a0e2534ee2': ['queued', 1789744316.308416], 'job_c99e9b79460e43a3': ['queued', 1789744322.199279], 'job_e1e1a0eb24654def': ['queued', 1789744324.758703], 'job_00f5be4f3b764b6f': ['queued', 1789744326.526914], 'job_5d6b13b522c44e00': ['queued', 1789744326.684071], 'job_709d4f54666549e2': ['queued', 1789744329.432805], 'job_5f2e32673b164b45': ['queued', 1789744334.443837], 'job_128dfa4ec5ba4b8b': ['queued', 1789744338.160779], 'job_84a0c2bc823249d1': ['queued', 1789744340.122471], 'job_6441ce0c25e14539': ['queued', 1789744343.184252], 'job_cdc66c4023db4140': ['queued', 1789744348.451583], 'job_95d5f6939d324f37': ['queued', 1789744350.174231], 'job_7def2f0e15e44fe5': ['queued', 1789744353.495645], 'job_2c2b07f7ca934ed8': ['queued', 1789744358.844174], 'job_6d335a43f9064790': ['queued', 1789744362.828845], 'job_a53b642e3df3415c': ['queued', 1789744368.38087], 'job_a39bbb83ffeb4798': ['queued', 1789744373.562728], 'job_4775eda380514d6e': ['queued', 1789744379.465182], 'job_5e8ede932b4f4299': ['queued', 1789744386.480449], 'job_7c13e9a0de9e4fb3': ['queued', 1789744391.26264], 'job_87ad08b6cf874b1d': ['queued', 1789744395.859613], 'job_57febf4f52154744': ['queued', 1789744397.382234], 'job_717ea1e1cc534ba9': ['queued', 1789744399.854424], 'job_b8d32584c57842a4': ['queued', 1789744405.07513], 'job_ca42ff4485fb4d38': ['queued', 1789744410.657285], 'job_72e4c811226844db': ['queued', 1789744414.865381], 'job_5583d6e6696b475d': ['queued', 1789744421.001385], 'job_cbcf92a9dbec44ef': ['queued', 1789744425.186828], 'job_5ee8e42c3cc5407c': ['queued', 1789744427.743159], 'job_c0ffa7c7bbc14911': ['queued', 1789744430.897909], 'job_5787b9d6af8349b3': ['queued', 1789744439.298311], 'job_0f6d12ad3e514030': ['queued', 1789744440.105619], 'job_5e709a004d8b422b': ['queued', 1789744445.823521], 'job_b5f38cf8736545e6': ['queued', 1789744450.665842], 'job_171305bccfc44ecf': ['queued', 1789744458.288006], 'job_09fe85cde55e436b': ['queued', 1789744463.033803], 'job_709dc6526ece4d85': ['queued', 1789744466.014765], 'job_a444eec763e34588': ['queued', 1789744469.900995], 'job_c441275aadc843ab': ['queued', 1789744476.258382], 'job_7b9c8b0b717e4aca': ['queued', 1789744479.849559], 'job_27b09e016ddd4ed8': ['queued', 1789744485.125469], 'job_f8530d7148a14574': ['queued', 1789744485.343375], 'job_1da2f0983f0746ff': ['queued', 1789744490.00573], 'job_9db3b75d66c741ba': ['queued', 1789744491.348514], 'job_94d0b3a98f1c4f73': ['queued', 1789744496.78699], 'job_64cabd1621b1412f': ['queued', 1789744502.961137], 'job_941c1762f52249e1': ['queued', 1789744506.880535], 'job_1af5ded42d434957': ['queued', 1789744509.662935], 'job_cf01053523914100': ['queued', 1789744512.098154], 'job_b6b114a6b6184c5e': ['queued', 1789744516.546435], 'job_dc2f0cb08a244da4': ['queued', 1789744521.869328], 'job_3ca8c309b6194f7e': ['queued', 1789744526.010662], 'job_ef55c9aaf7f64a92': ['queued', 1789744532.457866], 'job_2150f5fed7b54d29': ['queued', 1789744536.645546], 'job_52bff2f18ec84927': ['queued', 1789744541.004535], 'job_458d67fa75554edc': ['queued', 1789744544.159716], 'job_285199e5fdf643b8': ['queued', 1789744547.423872], 'job_c711a977beec4dcb': ['queued', 1789744548.404768], 'job_4c373bb66fab42b8': ['queued', 1789744552.088519], 'job_9949885ab94b45b7': ['queued', 1789744554.9489], 'job_08c9828f85f34553': ['queued', 1789744560.132202], 'job_7d94af90b0e04417': ['queued', 1789744562.691542], 'job_1a7a46ef49314650': ['queued', 1789744566.499232], 'job_0ff11c8526364a96': ['queued', 1789744571.535525], 'job_ea21f80b2e464935': ['queued', 1789744576.986815], 'job_ffa02df361e7459a': ['queued', 1789744580.007657], 'job_715bae82738548c1': ['queued', 1789744587.250981], 'job_0e1f748f7bc14691': ['queued', 1789744592.116947], 'job_8c9b851501c0432c': ['queued', 1789744596.313777], 'job_74dc7805438948d5': ['queued', 1789744600.261719], 'job_ebccde8c3da0465e': ['queued', 1789744606.974821], 'job_67a8f6e9b7664bcf': ['queued', 1789744611.870017], 'job_ea022778690145b6': ['queued', 1789744615.120282], 'job_d724f47dcfc645e5': ['queued', 1789744621.430485], 'job_a9710dd4aedb4b9f': ['queued', 1789744624.149328], 'job_3c94f9e21d684d18': ['queued', 1789744629.356736], 'job_e49fb0b791814d38': ['queued', 1789744633.948506], 'job_728fb7984ce94b57': ['queued', 1789744637.506285], 'job_0c34a73e68444f6b': ['queued', 1789744640.977206], 'job_d75e7effd14e457d': ['queued', 1789744644.460914], 'job_82f538961505472b': ['queued', 1789744647.929755], 'job_4ec844280f9d4dea': ['queued', 1789744651.007958], 'job_8f72670f3700491c': ['queued', 1789744655.540956], 'job_60cf15720e62481f': ['queued', 1789744658.993536], 'job_33ccaa2e3b2a42de': ['queued', 1789744663.118492], 'job_2de143afc2cc4033': ['queued', 1789744666.027396], 'job_0fcb1c66c742419b': ['queued', 1789744669.410254], 'job_831a100a88bc4cd7': ['queued', 1789744672.10965], 'job_636d399cf7144bc9': ['queued', 1789744873.788819], 'job_bad8cd5ee9e5418e': ['queued', 1789744909.384927], 'job_7e4af98c94d048e6': ['queued', 1789744984.060757], 'job_707585cd6530477c': ['queued', 1789745089.982687], 'job_4492f36ed1e44382': ['queued', 1789745212.859995], 'job_c3a94b902a1745d8': ['queued', 1789745251.370706], 'job_1b4e4b45a7844aae': ['queued', 1789745287.182804], 'job_97d455c58cf24ef1': ['queued', 1789745295.482129], 'job_31018b5fde7c4d2b': ['queued', 1789745312.55779], 'job_2fa37a7b4ade471d': ['queued', 1789745435.057787], 'job_7e073dbced2b4ee6': ['queued', 1789745505.439545], 'job_333d02fe32f749ab': ['queued', 1789745507.342775], 'job_775810d122854733': ['queued', 1789745594.8729], 'job_f8ada8b514dd4dc3': ['queued', 1789745640.704765], 'job_6e6de279a5ee4bc1': ['queued', 1789745654.853452], 'job_648776fee5f54cd0': ['queued', 1789745675.501089], 'job_ee6fe9a9d0c94c5a': ['queued', 1789745721.174395], 'job_d07b15d2d9994f9c': ['queued', 1789745752.479258], 'job_2a00b1ad2a7b424c': ['queued', 1789745755.246821], 'job_d091860e15604760': ['queued', 1789745829.130566], 'job_8117737080564ce0': ['queued', 1789745876.223713], 'job_09d4da60f9a445e7': ['queued', 1789745890.975689], 'job_43bb174e19e841ee': ['queued', 1789745913.26304], 'job_390df07a5cef44c6': ['queued', 1789745964.720886], 'job_a9730469b06d40b8': ['queued', 1789746024.0921], 'job_35e2c2485c8344f3': ['queued', 1789746043.560635], 'job_f427646a202f44b6': ['queued', 1789746053.89572], 'job_538d293ba94f475c': ['queued', 1789746109.252152], 'job_b949fc1bb48d44dc': ['queued', 1789746124.186149], 'job_c5dce4ce9dc24316': ['queued', 1789746191.992267], 'job_a22a6b930074458a': ['queued', 1789746233.676671], 'job_22b5d6e803074d0c': ['queued', 1789746268.648939], 'job_eae08dc920144623': ['queued', 1789746294.320914], 'job_08ed7004d757463f': ['queued', 1789803524.730696], 'job_b04cab19c53c45d8': ['completed', 1789811089.2172658], 'job_c1b793c1fe5d4066': ['failed', 1789811882.707009], 'job_8f214312452944d1': ['completed', 1789811923.592851], 'job_c5842c0402674a06': ['completed', 1789811969.321235], 'job_59180f95b258468f': ['completed', 1789812489.770598], 'job_70d0745302684b70': ['completed', 1789812603.15031], 'job_05f96c9ed0564d93': ['failed', 1789813359.310551], 'job_07c483b4c0154b0f': ['completed', 1789813516.599944], 'job_d8ed2ca08e154a4f': ['failed', 1789813989.805383], 'job_b889b2a9b9554155': ['completed', 1789814109.755036], 'job_b3122eac33134be0': ['failed', 1789816749.686739], 'job_0eee0394a1044ffc': ['completed', 1789816828.30659], 'job_380ca85420b446bb': ['completed', 1789816904.063489], 'job_710e1a11726c4376': ['completed', 1789816995.161073], 'job_c624f1495cd14a54': ['completed', 1789817085.185322], 'job_a5cfe86bac654bcc': ['completed', 1789817092.680869], 'job_4689df96bdd048a9': ['completed', 1789817165.737771], 'job_996c97e64e8a4fd3': ['completed', 1789817169.069193], 'job_26458acebd7f4a95': ['completed', 1789817242.618617], 'job_86f0a162ba55468d': ['completed', 1789817251.438527], 'job_d71bceba9e0043e8': ['completed', 1789817322.323734], 'job_c172ddf1bffc4b3b': ['completed', 1789817334.897794], 'job_ab0b81606a7c4a60': ['completed', 1789817401.509816], 'job_bfb4bc7770af42f1': ['completed', 1789817408.760912], 'job_3e99aec1609349d6': ['completed', 1789817475.462148], 'job_d9b2467fc7714cf1': ['completed', 1789817492.469038], 'job_466d3c808b594ae7': ['completed', 1789817570.022217]} 行 → after 301 行
旧记录逐条 status/updated_at 未变：0 条
新增：10 条 = 本次 10 个 root Job（无子 Job）
```

## 七、证据文件

- report：`/tmp/stability_report2.json`
- 分析：`/tmp/stability_analysis.json`（由 `/tmp/analyze_stability.py` 生成）
- 驱动：`/tmp/stability_run.py`（可用 `STAB_PREFIX` / `STAB_OUT` / `STAB_START` 复跑）
- 日志渠道：`/mnt/e/work/partner_workspace/state/outbound/replies.log` 第 19–28 行
- 读取入口：`python3 scripts/read_replies.py --workspace /mnt/e/work/partner_workspace --limit 10`
