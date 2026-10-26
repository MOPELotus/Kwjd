# 双端单集工作流

本文件、[决策记录](DECISIONS.md)与[回传清理](LIFECYCLE.md)是当前规则。旧聊天/旧样例不覆盖新用户指令；新决策立即更新配置/代码/文档，每独立步骤验证后commit/push并让另一端pull确认。

## 阶段

1. **接管与裁剪**：源视频和DaVinci工程保留；原片仅去片头片尾时stream copy。S01去前30秒、首个白帧为尾部线索，沿用已验证cutter，不重裁已有成果。动态发现集号，不限E49–55。
2. **字幕**：以已裁剪视频为时间基准，不再扣30秒；画面内容、原空格、不造标点。逐集真正审校完成后seal-srt，取回时验证SHA及ready sidecar。E49–55已于2026-10-26封存并取回，无需再跑OCR。
3. **背景**：split_background.py按各集裁剪视频实际时长，从长背景连续取段，E49-background.mp4等；保留背景源。无损切分略长时在时间线对齐节目尾部。
4. **抠像合成**：用户Magic Mask选人跟踪、风景+BGM合成；无法消除的硬字幕残片用户接受，不阻挡增强。Logo误留可在同节点加反向Power Window排除，避免误切人物/桌子，必要时仅问题时间段生效。共享PowerGrade同时保存Mask与窗口；重新抓静帧才保存新修改，新片重选点跟踪并检查窗口，不复用旧跟踪。
5. **混音**：沿用用户Fairlight混音。背景-14/节目+10dB只是该工程参考，不是普遍规范。用户接受偶发拍桌触顶、听感优先；峰值仅诊断，不擅自归一化/压缩/限幅/改增益或据此强制重导。
6. **直接导母片**：QuickTime MOV，HEVC/H.265 NVIDIA Main10，当前原生1920x1080/25fps，质量优先25000Kb/s起点、慢速/高质量；音频Linear PCM24bit/48kHz、Bus1 Stereo、归一化关闭。不烧新字幕、不做DaVinci超分/补帧/光流。不默认导巨大DNxHR；需重导则回DaVinci工程，不将错误DNxHR转码补救。
7. **版本与上传**：沿用masters/S01E49.clean-master.mov，不强制v2。新内容新SHA；旧哈希退役/删除授权存在本地.kwjd。远端同名异哈希不能直接覆盖，先协调退役并使旧增强检查点失效。确认渲染完成、稳定、媒体/解码/时长检查通过再原子上传。新鲜双端SHA匹配后，自动删除本地该导出母片及上传开始快照未变的D:/ResolveWork/CacheClip内容，保留目录和ResolveWork其他内容；变化时保留并报告。此后Windows可开始下一集Mask。
8. **独立增强**：真实backend技术验证、母片双端SHA验证、各文件系统峰值预算通过后，运行server_worker.py --once --episode S01E49 --stage enhance。用户已明确豁免E49的样片人工审批，可整集；保留技术短测和镜头切换处理，不以fps滤镜冒充AI。1080p60、时长不变。检查点绑定母片/后端/输出哈希；缺ASS也先增强，改样式不重跑增强。
9. **样式后烧录**：Codex做MiSans ASS并PotPlayer实画面预览，Aegisub可选；字体真实文件不进Git。样式/位置最终确认后生成绑定模板SHA的approved sidecar，再--stage all；模板存在不代表批准。ASS在AI之后烧录，原SRT不改写。
10. **最终编码**：HEVC NVENC1080p60+FLAC24bit/至少48kHz MKV，音频取原母片混音，不自动增益，原生96kHz以上保留实际率。不能声称编码恢复源音质，不能保证平台出现Hi-Res开关。
11. **回传清理下一集**：内部仍S01E49.final-1080p60.mkv；Windows按故事内回数命名“第一回 家门遭横祸，俊才跌凡尘.mkv”。远端报告SHA与本地一致、HEVC/60fps/FLAC/时长/首中尾解码检查、画面/字幕/同步质量检查完成，写接收收据；之后按已登记产物清单清理本集生成中间物及服务器成片副本，保留Windows最终片/来源/工程/封存字幕/人工证据/共用字体样式。云端每阶段验证下一步必要输入（包括原混音的无损交接）后释放前步中间物；只对服务器最终副本要求Windows验收收据。云端单集串行，Windows可并行准备下一集，Windows关机不应阻挡已接受云端任务继续到成片。

## 运行约定

- 用户授权双端Codex直接通过聊天或既有SSH alias TodayEatUbuntu-7K62协调，用户无需传话。云端后处理到成片全托管，自动选择可用存储及分块/检查点，除无法自主解决的真实阻塞外不反复问Windows。
- Linux测出可信整集耗时范围后主动汇报Windows，再由Windows通知用户；范围需基于实际母片的较长完整流程稳态实测，明确是否含烧录/编码/校验。Win离线时把报告和待通知状态持久保存，恢复后补交，不因联系失败丢失通知。
- 连续三次无法联系Windows且没有明确近期在线证据，按可能关机处理，尤其北京时间23:00–07:00；停止密集重试并退避，云端任务继续，不把离线记成媒体失败或停在等待Windows批准。保留最终片和收据，Windows恢复再回传验收；若有近期确实在线证据，才排查网络/服务故障。无需唤醒/重启Windows。
- df实时检查；不同挂载余量不可当作同一单文件空间，母片/模型/依赖/临时块/输出全部计峰值后仍留25GiB。post可映射用户可写存储，真实路径/余量/作业记录留本地。
- 线程定时监控只读进度与CPU/GPU/显存/温度/内存/磁盘，常态静默、完成/异常提醒；用户停止则暂停。忙碌/未响应时不点击、不自动停止重启或改工程。
- 失败保留证据；status之外检查post/jobs和检查点。代码存在不是backend可用、性能合格或已启动的证据。

## 命令

Windows：qqw.ps1的fetch-srt、sync-style、send-master、submit、fetch-final均可传-Episode 49。接收/清理见LIFECYCLE.md。

Linux：`python3 scripts/server_worker.py --once --episode S01E49 --stage enhance`先增强，样式批准后`--stage all`。SRT审校完成才`python3 kwjd.py seal-srt --file ~/qingqingwu/output/S01E49.zh.srt --approve`，不要批量制造封存。
