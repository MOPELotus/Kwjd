# 外部 Responses 字幕工具

2026-11-26用户明确授权剩余字幕交给隔离OMP持续处理，主会话每1或3小时检查；
本次选每小时，覆盖旧半小时频率和仅原生字幕代理的执行方式要求。
原生字幕代理先保存并暂歇，独立OMP是唯一新写入者，沿focused-v1及原seal/ASS门禁接续。

`run_subtitle_omp.py`用私有独立OMP配置/会话和环境白名单启动，
不继承Codex/OAuth或其他API凭据。`subtitle_omp_transport.ts`把模型网络请求限制到
已授权站点的Responses/模型列表，鉴权来源、模型及medium推理强度都须匹配；
实际HTTP状态、图像数量写私有传输日志，OMP会话保留真实response ID。
只启用read/bash/edit/write工具，不派子代理、不改媒体Worker。
普通文字和图像都由该外部API执行；App额度只影响主会话巡检及原生模型，
不暂停这个已授权外部API任务或实际媒体。外部服务计费以该站为准，OMP的本地价格元数据不是账单。

外部执行状态在数据根`ocr/subtitle-review/api-handoff/omp-status.json`，
唯一所有者声明在`.kwjd/subtitle-executor.json`。同一OMP会话可在有实际新进度时
接续，CLI出错或退出却无保存进度时停止并留待主会话检查，不无限空转重试。
13回完成标记还必须通过原有13份SRT/ASS验证才能结束。
定时器仍只用官方队列恢复同一个主会话，不唤醒已移交的原生字幕代理。

OMP直接使用read图像工具审校；首批原图复用下述已取证文件，单次非流式请求的502
不被当作已审或自动重试。下述直接Responses工具作为可选有界单批工具保留，
不得与唯一OMP同时审同一批。

`scripts/subtitle_review_responses.py` 是用户明确授权的外部图像审校工具，供同一个
字幕执行者使用；不启动常驻进程、不读取 Codex 登录、不调用 App 模型。
原生会话需先保存检查点并停止写入，再由独立 OMP 执行者接续。
OMP 与工具不得同时决定同一批字幕。

当前授权固定为 `https://api.zhehentiaohe.cn/v1/responses`、
`gpt-6.1-sol`、`medium`。密钥从私有 `~/.config/jigong/responses-api.key`
读取（仅所有者权限），只放内存鉴权；日志不保存密钥、请求头或任意网络错误正文。

```sh
post/face-venv/bin/python repo/scripts/subtitle_review_responses.py \
  --root /home/lotus/qingqingwu --episode S03E11 --start-index 864 --count 16
```

默认真实审图但不写决定。`--apply` 仅在所有条目文字可读、时间区间与实际 PTS 正常、
无未决疑点，且改动文字已获第二次真实图像请求一致复核时写入 journal 和工作稿。
图像生成、HTTP200、JSON格式通过都不能独自批准字幕。疑点返回退出码2，保存
具体问题，交给执行者局部补帧/必要声音辅助解决；不把不确定文字猜成通过。
此工具不封存、不做 ASS、不宣称整片逐帧检查。

数据根 `ocr/subtitle-review/responses/<episode>/<start>-v<attempt>/`
保存脱敏请求元数据、实际 response ID/模型、响应、判断和 batch 收据。
每条 journal 明确 `review_provider=authorized_external_responses_api`，
会话来源是实际 `responses:<response-id>`，不复用原生 Codex 看图声明。
已有成功响应可校验后复用；已有请求却没有响应时拒绝静默重发。
只有执行者明确判断可以再请求时才使用 `--attempt 2`（最多3），旧失败记录保留。
无自动重试，默认超时180秒；异常输出仅安全类型/HTTP状态。

执行前校验真实原片/接收收据SHA、原草稿SHA、当前精确下一cue，
持有唯一API写锁。最多16张原中帧，实际证据盘保留5GiB+0.5GiB。
缺失或身份变化、已有决定、部分失败均停下，不覆盖重跑。
已封存回目保持原样；后续完成仍按 focused-v1 的真实证据合同逐回 seal+ASS。

首轮接入实测：2026-11-26，S03E11原cue865–880已取证；真实POST得到HTTP502，
没有response ID，未计审过、未写决定，下一仍865。该失败不能作为接口审图成功证明。
