# 第一回持久成品交接

当前所有者是 tmux `kwjd-main` 中正常可见主CLI，接续主会话
`01a203ac-7e99-7153-a57a-e82c19cd6fd1`。主代理串行负责 S03E02 后期、成片、
实际质量验收、Windows收据和双端限域清理。仅一个原生字幕子代理负责13回审校、
真实SRT封存及ASS；实际parent/child ID记录在本地 `post/managed-S03E02/native-agents.json`。
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

本云端项目最新额度规则为真实剩余低于2%时仅可使用最近一张可用额度重置卡，
绝不消耗credits/点数；无可用卡或失败即保存现场停止。该规则覆盖本项目旧5%门槛，
不修改其他项目或私人全局规范。当前额度充足时不执行重置；实际重置机制由额度同事部署，
未到位不得臆造工具、卡片或凭据。
