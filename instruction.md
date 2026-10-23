# 换热站夜班交接前的表计归位

调峰结束以后，城北换热站的二次侧运行员发现换热站台账已经按表计出厂序列号记了三批热量，但现场支路标签在前一轮检修中脱落。现在只凭表计文件顺序汇总，会把高区回水支路的热量挪到低区，夜班交接时看不到真实的热损缺口。请你修好站内现有的 `secondary_heat_audit.py`，让下一批数据能够直接算出表计归属、热平衡和本批热损，并接着原来的批次台账落账。

平时由调度在站内执行下面的入口，输入目录是当天整理好的三份 JSON。运行成功时退出码为 0，标准输出和标准错误都不写内容；运行被拒时退出码为 2，标准输出为空，标准错误只有一行，以 `处理未完成: ` 开头，冒号后写原因，不得输出 Python traceback，也不得出现 `Traceback`。

```bash
python3 /app/secondary_heat_audit.py \
  --input /data \
  --result /output/secondary_heat_result.json \
  --ledger /output/batch_ledger.jsonl
```

## 今晚留下的三份记录

三份文件的成员面见下表；表外再出现成员，就整批退回。

| 文件 | 可以出现的成员 |
| --- | --- |
| `station.json` | `schema_version`, `station_id`, `batch_id`, `sample_interval_min`, `supply_temp_c`, `outdoor_temp_c`, `source_heat_kwh` |
| `branches.json` | `schema_version`, `branches` |
| `meters.json` | `schema_version`, `meters` |

站级版本字面量固定为 `secondary-heat-v1`；站号与批次号都不能是空串；采样间隔取 5 到 60 的整数，布尔值排除。`supply_temp_c`、`outdoor_temp_c` 和 `source_heat_kwh` 三列时序同长，每列 12 到 48 段，元素只收有限 JSON 数字。

支路账列出 2 到 18 项，每个支路项只允许 `branch_id`、`design_flow_m3h`、`valve_signature_m3h`、`wave_lag_min`；多出字段即退回。`branch_id` 是唯一、非空的字符串；`design_flow_m3h` 是大于零的有限数字；`valve_signature_m3h` 是四个有限数字组成的数组；`wave_lag_min` 是不小于零的有限数字。

表计项数目必须与支路项相同，每只表只允许 `meter_serial`、`valve_signature_m3h`、`wave_lag_min`、`flow_m3h`、`return_temp_c`、`demand_kwh`；多出字段即退回。`meter_serial` 是唯一、非空的字符串；阀门签名仍是四个有限数字；时延与三个历史数组的长度规则和站级数组相同。流量元素必须大于零，回水温度必须是有限数字，热量元素不得小于零。

三份文件都必须使用 UTF-8 编码和标准 JSON。对象任意层级出现重复键都要拒绝；缺键、额外键、错误类型、非有限数字和数组长度不一致同样拒绝。输入目录必须是真实目录，三份 JSON 必须是普通文件而不是软链接。目录中的其它文件不参与本批计算。命令给出的输入路径、结果路径和台账路径不能为空字符串；输入目录不可替换为普通文件或软链接。

## 表计归位

站内没有保存表计与支路的对照表。支路记录保留了四次阀门动作后的期望流量变化、温度波达时延和设计流量；表计记录保留了同一组响应、波达时延、历史流量、回水温度和本段热量。现场接线方向没有统一，所以表计阀门签名可能同向，也可能整组反向。归位时要把这两种方向都纳入比较，不能按文件名、序列号或记录顺序猜测。

计算前，支路按 `branch_id` 升序排列，表计按 `meter_serial` 升序排列。对任意一对支路和表计，`same` 方向直接比较两个四元素阀门签名，`inverted` 方向先把表计签名四个数取相反数再比较。阀门均方根误差是四组差平方的算术平均再开平方，距离越小越好；两个方向距离相同时取 `same`。历史流量先按数值升序取中位数，偶数个样本取中间两个数的算术平均。

这一对的归位代价为

```text
4.0 * 阀门均方根误差 / 0.60
+ 波达时延绝对差 / 2.0
+ 2.0 * abs(ln(流量中位数 / 设计流量)) / 0.15
```

其中 `ln` 是自然对数。支路与表计数量相同且一一配对，选总代价最小的完整配对；合法数据保证最优配对的第二优差值严格大于 `1e-9`，若只发现并列最小配对，就拒绝本批。方向字段仅允许 `same` 或 `inverted`，其它值退回。

分配记录里的 `valve_rmse` 是所选方向的阀门均方根误差，`lag_error_min` 是时延绝对差，`flow_log_error` 是 `abs(ln(流量中位数 / 设计流量))`，`assignment_cost` 是这一对按上式算出的代价。

## 热损反演

归位完成后，表计历史热量直接作为对应支路的末端需求。设第 `k` 个采样段的表计回水温度为 `r[k]`，室外温度为 `o[k]`，采样间隔为 `dt` 分钟。有效温差与滞后度时按

```text
lag_temp[k] = 0.55 * r[k] + 0.45 * r[k-1]
degree_h[k] = max(0.5, lag_temp[k] - o[k]) * dt / 60
```

计算；`k = 0` 时 `r[-1]` 取 `r[0]`。每个支路保留同一个有效传热系数 `ua`，并要求 `ua >= 0`。把全部支路的 `degree_h[k] / 1000` 按支路升序排成一行，就得到每个采样段的设计矩阵。对每个采样段，用站级输入热量减去该段所有表计热量之和，得到剩余损失。用非负最小二乘在这些采样段上反演各支路 `ua_w_per_k`，使残差平方和最小；不得用端到端固定温差代替滞后度时。

支路热损是 `ua_w_per_k` 与该支路全部 `degree_h` 乘积之和再除以 1000。聚合传热系数按支路度时加权计算：

```text
aggregate_ua_w_per_k =
  sum_branch(ua_w_per_k[branch] * sum_sample(degree_h[branch][sample]))
  / sum_branch(sum_sample(degree_h[branch][sample]))
```

分母为全部有效支路、全部采样段的度时总和；分母为零时聚合值写 `0.0`。这个聚合不是支路 `ua_w_per_k` 的简单算术平均。支路供入热量是表计需求与支路热损之和。站级供入、需求和热损分别取各段总和；`balance_error_kwh` 是站级输入热量减去需求总计与热损总计，保留正负号。

所有输出数字先按十进制四舍五入到小数点后六位，恰好半值时远离零取整；零不写成 `-0.000000`。结果中的时间样本数、支路数、排序和配对都来自本次输入。`assignment_summary.objective` 是最优配对总代价，`assignment_summary.second_best_margin` 是第二优完整配对总代价减去最优总代价。

## 交出去的格式

结果只交出一个对象，一级成员固定为八项且按表内顺序出现。`contract_version` 固定为 `secondary-heat-result-v1`。`assignments` 按支路升序，`branch_balance` 也按支路升序。各级成员如下：

| 对象 | 精确键集合 |
| --- | --- |
| 结果文件顶层 | `contract_version`, `batch_key`, `station_id`, `assignment_summary`, `assignments`, `branch_balance`, `system_balance`, `loss_inversion` |
| `assignment_summary` | `objective`, `second_best_margin` |
| `assignments[]` | `branch_id`, `meter_serial`, `orientation`, `valve_rmse`, `lag_error_min`, `flow_log_error`, `assignment_cost` |
| `branch_balance[]` | `branch_id`, `meter_serial`, `demand_kwh`, `loss_kwh`, `supply_kwh`, `flow_m3`, `ua_w_per_k` |
| `system_balance` | `source_kwh`, `demand_kwh`, `loss_kwh`, `balance_error_kwh` |
| `loss_inversion` | `samples`, `degree_hours_total`, `aggregate_ua_w_per_k`, `method` |

`loss_inversion.method` 固定写成 `lagged-degree-hours-nnls-v1`；`samples` 是采样段数。`flow_m3` 是表计历史流量乘以采样间隔分钟再除以 60 后求和。结果采用 UTF-8、紧凑 JSON：对象键之间和数组元素之间不加空格，字符串不转义非 ASCII 字符，文件末尾恰有一个换行字节。

## 批次台账

批次键按文件名 `branches.json`、`meters.json`、`station.json` 的字节升序依次取原始文件，计算 SHA-256。每份文件先写入 UTF-8 文件名，再写一个 NUL 字节、完整原始字节和另一个 NUL 字节，把三段结果顺序送入同一个摘要。计算期间不得重新序列化 JSON。

台账是 UTF-8 JSONL 文件；文件不存在或为空时视为空台账。每一行必须是以 `{` 开头、以 `}` 结尾的紧凑 JSON，并以一个换行结束。台账摘要不能多加字段；六个成员及其含义见下表。前三个是字符串，后三个是有限数字。台账中出现重复批次键且结果摘要相同，就保持整份台账字节不变；同一批次键对应不同结果摘要时拒绝本批。

| 台账成员 | 含义 |
| --- | --- |
| `batch_key` | 三份原始输入的批次摘要 |
| `station_id` | 本批换热站号 |
| `result_sha256` | 本批结果完整字节摘要 |
| `source_kwh` | 站级供入热量 |
| `demand_kwh` | 表计末端需求总计 |
| `loss_kwh` | 反演得到的热损总计 |

新批次只追加一行，不得重写或搬动已有行；本批结果摘要取结果文件完整字节的 SHA-256。若同一结果文件和台账文件再次处理完全相同的输入，结果字节与台账字节都必须保持不变。

结果和台账要一起发布。先校验整批输入和旧台账，再在各自父目录写临时文件并刷新到磁盘；结果替换成功后，台账替换失败时，必须把结果恢复成运行前字节，若运行前没有结果就删除本次结果。任何失败都不能改动输入、不能留下临时文件、不能留下半行台账。两个目标所在的父目录必须预先存在；台账目标不能是目录。

## 会被接下来的班次重复使用

程序会在不同批次之间连续运行，输入目录、结果路径和台账路径通过命令行传入，不能把本班数据、样例结果或固定哈希写进代码。结果文件按本次输入重建，台账只允许追加。输入、结果或台账被拒绝时，都应该给出可以定位到业务原因的说明，而不是崩溃堆栈。

一个十八支路批次从进程启动到两个文件发布完成，在这台站机上应在 8 秒内结束；这是交接批处理的公开上限。
