# 最终回传、清理、下一集

## 文件名

内部ID始终S01Exx。Windows最终成片按源文件里的故事回数和回目标题命名：`第一回 家门遭横祸，俊才跌凡尘.mkv`。这里E49是“卿卿误我（一）”，不是第49回。不按固定E49–55硬编码；从manifests/InputName或现存视频名发现，保存小型元数据到.kwjd/catalog，素材临时副本清理后仍能复现命名。缺回数/标题时明确补元数据，禁止猜名和覆盖另一集。

## 收据门禁

`qqw.ps1 fetch-final`取内部S01Exx成片和服务器report，按故事名命名本地文件，SHA必须与report一致；复查HEVC1080p60/FLAC24bit>=48k/时长、首中尾解码。它写Windows接收收据，不把“存在”当“完成”。

接收后做实际画面边缘、字幕排版、嘴型同步和声音检查；检查通过记录quality_checked。用户已接受的硬字幕残片/拍桌触顶不算失败，不擅自改混音。收据绑定ep、本地最终绝对路径、SHA、技术检查、质量检查。服务器必须拿到匹配收据才能删除其最终副本。

## 清理范围

用户最新持续授权：逐阶段校验交接后，清理上一步不再需要的产物，避免累积母片和缓存。只有服务器最终成片的删除必须等Windows接收验收；不要把这道门禁套到所有中间物。

1. 本地母片技术检查、原子上传、双方新鲜SHA匹配后，保存上传收据并删除本地该导出母片；只清`D:/ResolveWork/CacheClip`内容，保留目录、`.gallery`及其他ResolveWork内容。开始上传先快照，清理前两次复核；新Mask或缓存变化就保留，不误删新工作。
   本地释放后向`post/receipts/S01Exx.upload.json`发送小型确认，绑定episode/SHA、verified和local_master_deleted。Linux释放incoming前还须验证该确认，避免在Windows仍做fresh SHA核验时提前删远端母片。`send-master`自动发送，异常恢复可用`confirm-upload -Episode 49`幂等补发；它不重新上传媒体。
2. 上一步完成后Windows可开始下一集Mask；不必等云端上一集最终回传。`gate-next`只检查此前本地上传释放，不以Windows最终接收阻挡云端已接任务。
3. 云端母片释放前，必须保留下一步所需画面输入和原始混音的无损音频交接，校验完整解码PCM等价、哈希、时长；检查点绑定母片SHA。每步成功后才清上步，失败保留恢复必要输入和证据。
4. 云端独立全托管至增强、补帧、ASS烧录、最终验证；一台Worker一次一集。自动选择用户可写挂载，逐文件系统计模型/输入/临时/输出峰值仍留25GiB。Windows夜间关机不阻挡云端处理；只在无法自主解决时通知。
5. 服务器最终片保留到Windows按回目命名回传、SHA/媒体/实际质量验收并给出收据，再清其最终副本和剩余已登记产物。最终ASS、审校报告和小型审计证据保留。

跨文件系统存储：`post`本身可以是配置好的链接。`post/incoming`、`post/cache`、`post/final`区域根也可链接到其他存储，但须在本机`.kwjd/storage.json`声明`{"schema_version":1,"areas":{"incoming":"绝对目录","final":"绝对目录"}}`，解析后必须精确匹配。区域内部的符号链接/目录联接一律拒绝登记和清理。真实路径只留本地，不提交Git。
现有后端使用的`post/backend/storage.json`（同schema_version，字段为`zones`）也可直接作为声明，无需复制一份。两份并存时必须一致；不得用备用配置绕过不匹配的现有声明。

- 可清：已登记的本集masters导出、Exx-background切片、post/incoming母片副本、post/cache/S01Exx增强/临时块、post/final服务器副本、本集work/S01Exx临时输出。未登记、其他集、活跃任务、路径越界或哈希不符均拒绝。
- 必留：Windows最终成片及收据、原始节目/长背景、DaVinci工程和PowerGrade、封存SRT/ready、最终ASS/模板/字体、人工审校决定和报告、源代码/Git、仍在用的环境。旧cut视频及OCR input不在默认清理清单，需先证明另一份来源完好并明确登记，不能删整个input/output/work。
- 最终回传清理脚本默认只列计划，--execute才删；重新验证Windows最终片SHA与质量收据，Linux另验证服务器成片SHA和收据，检查作业不活跃并持Worker锁。阶段释放分别用上传收据或下游交接证据，不伪造最终接收收据。
- Windows可用Remove-Item -LiteralPath或Python原生文件API，先解析并核对绝对路径；禁止跨shell拼删除、通配符泛删、跟随符号链接清目录。
- 若用户明确退役某个失败母片（本次E49），记录该次哈希与授权、只删除明确指定输出，不等同完成整集，不允许因此清理其他素材。

## 同步与当前状态

每次新约定必须同步修改AGENTS/决策文档/配置/实现；独立步骤验证后立即commit/push，另一端fetch+普通merge/pull确认HEAD。实际媒体、收据和绝对环境路径不进Git。

当前E49新HEVC已通过本地技术验证并上传中；上传校验后自动清本地母片/CacheClip。MiSans84新母片预览已获批准，云端可在增强完成后直接烧录。阶段状态以真实收据为准。
