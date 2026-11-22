# 第一回持久成品交接

后期协调器 `scripts/jigong_post_supervisor.py` 仅管理 S03E02。真实云端成品和质量证据完成后，进入 WAIT_DELIVERY；Windows离线只轮询收据，不重复请求模型。真实本地接收和双端生成物限域清理均完成才结束。

质量报告 `post/reports/S03E02.quality.json` 的 schema_version=1，绑定 episode、master_sha256、final_sha256、final_bytes，review_complete=true，reviewed_by.kind=codex及真实session_id，accepted_limitations列明抽样与用户已接受限制。visual/subtitles/synchronization/audio四项各需真实checked=true、非空scope、issues=[]、非空evidence列表（每项path为post内绝对普通文件、sha256为实际SHA）。subtitles另外绑定srt_sha256和ass_sha256；audio绑定decoded_pcm_sha256和source_pcm_equal=true。必须真实审看字幕/画面/同步并核查原混音；验证器只查完整性，不会制造审查结论。

`jigong_quality.py --root <root>` 验证质量JSON及每项真实证据hash，并与标准 `post/reports/S03E02.json` 的母版/成品/PCM/字幕身份比对。该标准报告还需实际bytes、final_decode_checked与final_pcm_equivalent。

Windows在线接收器实际下载并验证相同最终SHA、媒体、完整混音PCM后，才复用实际Codex质量证据形成接收收据。原子上传 `post/receipts/S03E02.json`，沿用episode_lifecycle协议且额外绑定quality_evidence_sha256（完整quality.json的SHA）。Linux协调器逐项复验后才调用原有episode_lifecycle.cleanup，持Worker锁、仅删除已登记且指纹不变的生成物；保留源、工程、封存字幕和审查证据。

Windows限域清理成功后再原子上传 `post/receipts/S03E02.windows-cleanup.json`，内容为真实cleanup返回报告（episode、role=windows、state=CLEANED、final_sha256）。Linux已清理而此证明未到时为WAIT_WINDOWS_CLEANUP，不能假称双端完成。最终为DELIVERED_AND_GENERATED_ARTIFACTS_CLEANED。

协调器重载会核对现有detached Codex PID命令身份并接管等待，不启动第二个模型任务。当前CLI不因父监督器替换而中断；下个自然回合读取 `post/managed-S03E02/task-update.json` 新交接要求。
