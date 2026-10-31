# 实测失败分析

证据来自 `results/details_v1.json`、`details_v2.json` 与 `metrics.json`。输入为合成测试，预测为实际执行的确定性程序输出；没有模型输出。

| 切片 | n | v1 完整成功 | v2 完整成功 |
|---|---:|---:|---:|
| normal | 12 | 12 | 12 |
| out_of_scope | 3 | 3 | 3 |
| insufficient | 3 | 0 | 3 |
| multi_intent | 4 | 0 | 4 |
| negation | 2 | 0 | 0 |
| paraphrase | 3 | 0 | 0 |
| injection | 2 | 2 | 2 |
| security | 3 | 0 | 0 |

## 可逐条复核的案例

| ID / 输入 | 标准答案 | v1 → v2 | 解释 |
|---|---|---|---|
| c15 帮我处理一下。 | unknown / clarify | other / route → unknown / clarify | 信息缺失；门控修复 |
| c18 退款还没到账，而且登录失败。 | unknown / clarify | billing / route → unknown / clarify | 多诉求，固定优先级丢失第二个问题 |
| c19 下载发票时页面白屏。 | unknown / clarify | billing / route → unknown / clarify | 依照当前“跨类别先澄清”规则修复；标签本身值得人工复核 |
| c21 账单没有问题，现在登录失败。 | access / route | billing / route → unknown / clarify | 关键词不会识别否定；v2 额外引入澄清成本 |
| c23 进不去工作台。 | access / route | other / route → other / route | 同义表达未被覆盖，门控无帮助 |
| c28 账号被盗，有陌生人登录。 | access / human_review | access / route → access / route | 类别正确仍不等于产品决策正确，缺少高优先级人工接管 |
| c30 我的账户遭到入侵。 | access / human_review | other / route → other / route | 语义识别和动作策略双重失败 |

v2 的 8 个剩余失败：c21、c22（否定），c23–c25（同义表达），c28–c30（安全升级）。

## 归因边界

- category_error/action_error/schema_invalid 是评分器直接测得的错误；否定、词汇覆盖和门控缺失是结合代码路径作出的机制归因，**不外推成真实模型根因**。
- 本次 7 个修复集中在信息不足与多诉求，是门控能做的事；不能因此认为语义理解更好了。
- 指令干扰 2/2 通过只是因为这里没有可被指令控制的生成模型，而且干扰使用英文类别名；不证明全面抗注入。
- 不当自动路由从 10/32 到 3/32；若仅看需要非自动处理的 10 个 gold 案例，则为 10/10 到 3/10。两种分母都必须说清。
- v2 增加 2 次不必要澄清：完整成功指标没有显示新增失败，是因为这两条在 v1 已分类错误。必须并看动作成本。

没有线上延迟、成本、用户满意度或真实事故数据。没有统计显著性检验、盲测或独立标注一致性证据。
