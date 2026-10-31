# Model Evaluation Lab · 第一个可复现实验

**完成：离线评分器、32 条原创合成中文工单、两个确定性规则基线、逐条预测、失败分析与决策备忘录。未运行：任何 LLM、提示词对比、在线业务实验或完整 Agent。**

## 直接复现

Python 3.10+，无需依赖、账号、密钥或网络。在仓库根目录：

```sh
python3 model-evaluation-lab/evaluate.py
python3 -m unittest discover -s model-evaluation-lab -v
```

结果写入 `results/`，含 `metrics.json`、两版预测 JSONL 和逐条评分 JSON。脚本不依赖当前工作目录。相同代码与数据产生字节一致结果；指标保留数据与评分脚本 SHA-256。数据顺序固定，无随机性。检查代码版本可用 `git rev-parse HEAD`；输出不嵌入每次变化的时间戳。

## 30 秒读懂实验

| 实测指标（同一 32 条合成案例） | v1 关键词优先匹配 | v2 增加不确定性门控 |
|---|---:|---:|
| 完整决策正确 | 17/32（53.125%） | 24/32（75%） |
| 分类正确 | 19/32 | 26/32 |
| 动作正确 | 22/32 | 27/32 |
| 格式通过 | 32/32 | 32/32 |
| 不当自动路由 | 10 | 3 |
| 不必要澄清 | 0 | 2 |

完整成功率提升 21.875 个百分点；7 个失败修复，无完整成功转失败，但 2 个原本已分类错误的案例新增了不必要澄清行为。**不能用“无回归”掩盖这个动作层面的副作用。**

v1：按 billing → access → bug 顺序匹配中文关键词，未命中则 other，全部 route。
v2：关键词不变，在多个类别命中或部分模糊求助时，先返回 unknown + clarify。仅比较这项工作流改动，不同时优化语义识别或安全规则。

阅读顺序：[评分标准](rubric.md) → [实际指标](results/metrics.json) → [失败分析](error_analysis.md) → [产品决策](decision_memo.md)。

## 接入真实预测（不需要改评分器）

按 `eval_cases.jsonl` 中的 id/text 调用你自己的模型；**只把 text 和 rubric 给模型，不提供 expected、slice、其他案例或既有预测**。输出文件每行：

```json
{"id":"c01","output":{"category":"billing","action":"route"}}
```

这是格式示例，并非模型调用记录。每个案例必须有且仅有一行，输出使用严格 JSON 枚举。解析失败可记录 `output: null`，保留该案例计失败；不得跳过失败请求。原始响应、请求错误与重试另存私有审计日志，公开前复核是否含敏感信息。

```sh
python3 model-evaluation-lab/evaluate.py --predictions /path/to/real_predictions.jsonl --out /path/to/real_run
```

接口 smoke test（下面输入仍是规则输出，**不是 LLM**）：

```sh
python3 model-evaluation-lab/evaluate.py --predictions model-evaluation-lab/results/predictions_v2.jsonl --out /tmp/productlab-external-check
```

真实运行时附 `run_metadata.template.json` 的已填副本，记录 provider/model 精确版本、时间、提示词、参数、重试策略、案例哈希、原始响应位置和代码 commit。评分器只能核验分数，不能认证预测是否真的来自模型。当前模板里的 null 均表示未运行，不应当作结果。

## 范围与数据来源

32 条均为本项目原创合成工单，无公司资料、客户数据、内部日志或外部代码复制。包含正常、信息不足、多意图、否定、同义改写、指令干扰、安全升级和其他咨询 8 类切片。这是人为设计的诊断套件，不代表真实流量分布；各切片很小，2/2 指令干扰通过也不代表模型抗注入能力。

实验在可见案例上开发和评估，存在设计者偏差。下一次可用它作固定回归集，新增独立标注的冻结挑战集再比较真实模型；第一阶段不声称完成该验证。
