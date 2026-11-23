# 第一回持久成品交接

2026-11-23用户通过原Windows制作会话明确续接第二回S03E03：真实母片收据到达并核验后，
保持首回后的串行生产，不等Windows聊天再次发命令。第二回独立核对封存SRT、ASS、实际母版时钟、
样式、逐文件系统峰值预算、质量证据和接收/清理收据；不能继承首回质量结论或凭上传进度准入。
现有纯工具巡检观察`post/managed-S03E03/progress.json`及真实handoff，必要时接续同一主对话；
不新增生产Worker或字幕代理。实际第二回到货后再按证据扩展生产profile及质量/交付入口。

第三回S03E04同样已授权在前两回后串行续接。另盘接收根登记在本地
`.kwjd/queued-master-storage.json`；巡检读取该根下的真实handoff后唤醒主代理核验，
上传进度和收据存在本身都不是生产准入。E04按本地SHA锚定预清理的缓存不再重复清理，
后续E05等新一回已经开始生成的Windows缓存不得受前一回收据/旧快照影响。

当前所有者是用户在云端继续的可见主会话
`01a203c2-2956-74e1-a574-bfb03258d250`，接续已中断并归档的主会话
`01a203bf-c3d2-7052-837c-576e3f05bcd4`。主代理串行负责 S03E02 后期、成片、
实际质量验收、原 Windows 制作电脑的收据和双端限域清理；接收端不能凭当前聊天设备
或任意可连接的 Windows 主机推定，必须核对原项目目录与接收配置。仅一个原生字幕子代理
负责13回审校、真实SRT封存及ASS；实际parent/child ID记录在本地
`post/managed-S03E02/native-agents.json`。旧树的 running 字段是历史状态；当前树只保留
一个接管子代理，继承审校检查点，不重启已完成的OCR或丢弃已审决定。
不能使用 `codex exec`、nohup或旧监督器另开模型服务。两个旧supervisor仅保留历史与纯工具代码，
不得重新启动其exec循环，不修改会话数据库或source字段。真实云端成品和质量证据完成后，
进入 WAIT_DELIVERY；Windows离线只合理等待收据，不空跑模型。真实接收和双端生成物限域清理
均完成才结束，母版遵守项目保留策略。

质量报告 `post/reports/S03E02.quality.json` 的 schema_version=1，绑定 episode、master_sha256、final_sha256、final_bytes，review_complete=true，reviewed_by.kind=codex及真实session_id，accepted_limitations列明抽样与用户已接受限制。visual/subtitles/synchronization/audio四项各需真实checked=true、非空scope、issues=[]、非空evidence列表（每项path为post内绝对普通文件、sha256为实际SHA）。subtitles另外绑定srt_sha256和ass_sha256；audio绑定decoded_pcm_sha256和source_pcm_equal=true。必须真实审看字幕/画面/同步并核查原混音；验证器只查完整性，不会制造审查结论。

`jigong_quality.py --root <root>` 验证质量JSON及每项真实证据hash，并与标准 `post/reports/S03E02.json` 的母版/成品/PCM/字幕身份比对。该标准报告还需实际bytes、final_decode_checked与final_pcm_equivalent。

Windows在线接收器实际下载并验证相同最终SHA、媒体、完整混音PCM后，才复用实际Codex质量证据形成接收收据。原子上传 `post/receipts/S03E02.json`，沿用episode_lifecycle协议且额外绑定quality_evidence_sha256（完整quality.json的SHA）。Linux协调器逐项复验后才调用原有episode_lifecycle.cleanup，持Worker锁、仅删除已登记且指纹不变的生成物；保留源、工程、封存字幕和审查证据。

Windows限域清理成功后再原子上传 `post/receipts/S03E02.windows-cleanup.json`，内容为真实cleanup返回报告（episode、role=windows、state=CLEANED、final_sha256）。Linux已清理而此证明未到时为WAIT_WINDOWS_CLEANUP，不能假称双端完成。最终为DELIVERED_AND_GENERATED_ARTIFACTS_CLEANED。

主CLI沿用当前历史和检查点，每实际阶段更新 `post/managed-S03E02/progress.json`，
读取 `task-update.json` 的新交接要求。字幕旧独立会话仅只读接管历史；原生工具不能恢复独立
会话时，只创建这一个原生字幕子代理，不伪称外部exec为子代理。

2026-11-22 用户随后明确并再次确认云端固定规则：第一回真正挂上生产后，
采用约30分钟一次的定时触发检查，不为轮询一直保持模型对话活跃；后续回目同样执行。
实际账户额度低于2%时保存检查点、暂停主对话及模型字幕审校，后台真实媒体处理进程
继续运行，不因模型额度杀Worker、推理或编码。无自动重置卡或点数回退。
无法查明额度时记录错误，不发起定时模型唤醒；纯工具状态检查和实际媒体作业可继续。
该规则只限云端，不改变Windows自己的规则。原制作电脑已由用户确认是lotuspc；
它当前离线，交接通知保留待发送，上线后须同步并核对其Git HEAD，未送达不称已通知。

当前可用连接工具没有原生聊天定时器接口。云端采用用户systemd定时器运行版本化
纯工具巡检；需要接续判断时用官方 `codex queue --thread ... --message ... --resume`
向当前可见会话排队，不启动 `codex exec` 或旧模型监督器。巡检记录真实状态、额度和
排队/恢复结果；排队成功不等于恢复成功。等待第一回SRT真实封存期间不绕过字幕或
母版时钟门禁；巡检可在输入就绪时接续主代理，不能把“等待输入”称为已开始生产。


固定工作栈已按用户纠正恢复：`config/workflows/native-face-v1.json`和完整requirements锁确定GFPGAN/A4000、强类型TensorRT RIFE/T10及overlap1。ONNX生产准入已撤回。新TensorRT引擎按归档原配方真实重建，模型图SHA与旧版一致；当前母片900原生→1800输出帧、901含前瞻修脸、两处cut、完整解码及PCM等价短测已通过，并实际看过六组源/输出图。实际引擎/样片SHA和视觉证据留post，不进入Git。恢复后的后端准入已启用，整回仍等待第一回真实封存SRT和ASS。之后主代理使用`run_fixed_post.py --master ... --ass ...`完成门禁并提交唯一后台Worker，不再临时选择技术栈。
