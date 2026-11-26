# 最终回传、清理、下一集

用户明确要求失败Mask清缓存重跑时，使用`resolve_cache.py cleanup-for-rerun --root <数据根> --episode <EP> --reason <用户本次原话> --execute`。
此路径只释放固定CacheClip内容，仍先验证路径无链接/越界、两次快照不变；保留目录、工程和其他素材。
它记录独立FAILED_MASK_CACHE_RELEASED_FOR_RERUN收据，不要求或伪造有效母版/上传证明，不可代替正常交接清理。

## 文件名

内部ID始终S01Exx。Windows最终成片按源文件里的故事回数和回目标题命名：`第一回 家门遭横祸，俊才跌凡尘.mkv`。这里E49是“卿卿误我（一）”，不是第49回。不按固定E49–55硬编码；从manifests/InputName或现存视频名发现，保存小型元数据到.kwjd/catalog，素材临时副本清理后仍能复现命名。缺回数/标题时明确补元数据，禁止猜名和覆盖另一集。

## 收据门禁

返工成片使用独立下载暂存与新版SHA：`accept_reworked_final.py --root <数据根> --state <回传状态JSON> --report <远端READY报告> --publish`。脚本实际验证SHA、媒体、首中尾解码及完整原混音PCM哈希，然后比较旧版本哈希、归档旧收据并原子替换同名本地成片；不继承旧质量批准、不自动设quality_checked。普通fetch-final仍可能指向旧版，不能混用。

2026-10-29用户明确要求取回新版后清理第一回双端非成品产物，保留双端新版成片，再上传第二回并在远端SHA匹配后删本地第二回母片。新版回传技术证据与显式退役授权允许释放旧返工来源及已登记的可重建中间媒体，不等于删除新版服务器成片或宣布全片主观质量通过。Windows使用`release_local_episode_media.py --root <数据根> --episode S01E49 --execute`，只处理本集work/预览的生成媒体及本集背景切片，逐文件SHA核对，保留PNG/JSON/日志证据、原始节目、工程、字幕和共用字体。第三回CacheClip正在使用，上传第二回必须识别其已提前清理的哈希收据，绝不再次快照或清理当前缓存。

`qqw.ps1 fetch-final`取内部S01Exx成片和服务器report，按故事名命名本地文件，SHA必须与report一致；复查HEVC1080p60/FLAC24bit>=48k/时长、首中尾解码。它写Windows接收收据，不把“存在”当“完成”。

接收后做实际画面边缘、字幕排版、嘴型同步和声音检查；检查通过记录quality_checked。用户已接受的硬字幕残片/拍桌触顶不算失败，不擅自改混音。收据绑定ep、本地最终绝对路径、SHA、技术检查、质量检查。服务器必须拿到匹配收据才能删除其最终副本。

## 清理范围

2026-10-29用户扩大持续清理授权：完整原视频在D盘第一季母盘时，已导出回目的去片头尾裁剪副本和背景切片都可重建、可释放；本次Windows明确包括E49–E52，不包括正在制作的E53。先验证完整原视频存在、时长/首中尾解码和SHA，保留原片/裁剪副本SHA、原manifest与背景split映射、回目命名和可重建参数，再只删精确副本。使用`release_working_copies.py --root <数据根> --original-root <完整原片目录> --episodes S01E49 S01E50 S01E51 S01E52 --include-background-slices --execute`。完整原片、长背景源、工程、母片/最终片、封存字幕和小型证据仍保留；用户接受旧达芬奇时间线重导前需重切和重新链接。服务器同样可阶段清理已无下游依赖的cut/OCR输入复制、过期测试/构建缓存等，但仍在使用或下一步时钟核验/优化需要的输入必须先留到依赖完成。不得将只有Windows存在的母盘说成云端备份，或因清理而让Windows离线时云端任务无法继续。

用户最新持续授权：逐阶段校验交接后，清理上一步不再需要的产物，避免累积母片和缓存。只有服务器最终成片的删除必须等Windows接收验收；不要把这道门禁套到所有中间物。

2026-10-28用户进一步授权：本地母片已完成技术检查、SHA锚定且确认导出结束后，可以在上传前先释放CacheClip，让Windows继续下一回Mask；本地母片必须保留到远端新鲜SHA匹配。使用`resolve_cache.py cleanup-local --root <数据根> --episode S01Exx --execute`，要求本地`.kwjd/cache-local-approvals/S01Exx.json`显式授权并绑定母片SHA、匹配的导出检查记录、时长通过或同SHA的用户例外批准；执行前重新哈希母片和两次比对缓存快照，拒绝链接/越界/变化。只清D:/ResolveWork/CacheClip内容，保留目录、.gallery和其他ResolveWork内容；正在生成新Mask缓存时不得清理。本地锚定不代表已上传或服务器接收，不能伪造上传收据。

用户要求先完成上一回上传/本地母片释放，再清下一回缓存时，使用一次性`cache_after_upload.py prepare/run`串接：准备时冻结当前缓存快照与下一回母片SHA，后台等待原上传进程退出，验证上一回双份上传/释放收据和本地母片已删除，再重新验证下一回母片及冻结缓存后清理。等待期间缓存变化即拒绝清理，不用完成时才创建的新快照误删更后一回Mask；下一回母片仍保留到自身上传校验完成。状态与日志留本地，不创建定时轮询任务。

1. 本地母片技术检查、原子上传、双方新鲜SHA匹配后，保存上传收据并删除本地该导出母片；只清`D:/ResolveWork/CacheClip`内容，保留目录、`.gallery`及其他ResolveWork内容。开始上传先快照，清理前两次复核；新Mask或缓存变化就保留，不误删新工作。
   本地释放后向`post/receipts/S01Exx.upload.json`发送小型确认，绑定episode/SHA、verified和local_master_deleted。Linux释放incoming前还须验证该确认，避免在Windows仍做fresh SHA核验时提前删远端母片。`send-master`自动发送，异常恢复可用`confirm-upload -Episode 49`幂等补发；它不重新上传媒体。
2. 上一步完成后Windows可开始下一集Mask；不必等云端上一集最终回传。`gate-next`只检查此前本地上传释放，不以Windows最终接收阻挡云端已接任务。
3. 云端母片释放前，必须保留下一步所需画面输入和原始混音的无损音频交接，校验完整解码PCM等价、哈希、时长；检查点绑定母片SHA。每步成功后才清上步，失败保留恢复必要输入和证据。
4. 云端独立全托管至增强、补帧、ASS烧录、最终验证；一台Worker一次一集。自动选择用户可写挂载，逐文件系统计模型/输入/临时/输出峰值仍留5GiB。Windows夜间关机不阻挡云端处理；只在无法自主解决时通知。
5. 服务器最终片保留到Windows按回目命名回传、SHA/媒体/实际质量验收并给出收据，再清其最终副本和剩余已登记产物。最终ASS、审校报告和小型审计证据保留。

跨文件系统存储：`post`本身可以是配置好的链接。`post/incoming`、`post/cache`、`post/final`区域根也可链接到其他存储，但须在本机`.kwjd/storage.json`声明`{"schema_version":1,"areas":{"incoming":"绝对目录","final":"绝对目录"}}`，解析后必须精确匹配。区域内部的符号链接/目录联接一律拒绝登记和清理。真实路径只留本地，不提交Git。
现有后端使用的`post/backend/storage.json`（同schema_version，字段为`zones`）也可直接作为声明，无需复制一份。两份并存时必须一致；不得用备用配置绕过不匹配的现有声明。

- 可清：已登记的本集masters导出、Exx-background切片、post/incoming母片副本、post/cache/S01Exx增强/临时块、post/final服务器副本、本集work/S01Exx临时输出。未登记、其他集、活跃任务、路径越界或哈希不符均拒绝。
- 必留：Windows最终成片及收据、原始节目/长背景、DaVinci工程和PowerGrade、封存SRT/ready、最终ASS/模板/字体、人工审校决定和报告、源代码/Git、仍在用的环境。旧cut视频及OCR input不在默认清理清单，需先证明另一份来源完好并明确登记，不能删整个input/output/work。
- 最终回传清理脚本默认只列计划，--execute才删；重新验证Windows最终片SHA与质量收据，Linux另验证服务器成片SHA和收据，检查作业不活跃并持Worker锁。阶段释放分别用上传收据或下游交接证据，不伪造最终接收收据。
- Windows可用Remove-Item -LiteralPath或Python原生文件API，先解析并核对绝对路径；禁止跨shell拼删除、通配符泛删、跟随符号链接清目录。
- 若用户明确退役某个失败母片（本次E49），记录该次哈希与授权、只删除明确指定输出，不等同完成整集，不允许因此清理其他素材。

## 同步与当前状态
CacheClip快照对小型`Info.txt`记录内容SHA；仅在内容、大小及路径集合不变时，允许Resolve重写同内容
导致的Info时间戳及其祖先目录时间戳变化。其他目录变化、媒体mtime/大小变化、新增/删除路径仍阻止清理；
旧快照没有Info内容SHA时保留原严格规则。该处理用于避免项目元数据自动保存阻塞已验收本回的预清理，
不放宽下一回活跃缓存保护。
用户在第三回（S03E04）再次明确时间优先：完成本地导出检查并新鲜SHA锚定后，优先释放本回CacheClip，
让Windows立即开始下一回；母版继续保留并独立上传。后续上传收据处理必须识别同SHA的local-anchor清理收据，
不再次快照或删除新一回缓存。`cache_after_master_handoff.py prepare --remote-root <已核实接收根>`
支持云端另盘的独立接收根；此模式要求已有匹配预清理收据，只校验明确路径的实际handoff，不清新缓存。
后续母版可以在上一回生产期间完成云端接收，但不得并发生产。
接收许可必须持续写到现有上传器实际等待的`remote_root/post/handoffs/EP.upload-admission.json`，
不能从旧的另盘排队记录推断上传器已经换根；改变接收根前须两端核实且没有活跃上传。
`jigong_receive_admission.py`保留每盘5GiB，并按真实生产根预算当前整链剩余峰值。
已经占盘的本回已完成编码片段，只在母版/后端指纹匹配、单链接普通文件及SHA通过时
扣除对应物理占用，最多扣除各阶段均包含的cache视频预算，避免与当前free重复预留。
未完成、其它回目、symlink/hardlink和身份变化的片段不获得抵扣；校验缓存绑定inode/大小/mtime/ctime，
文件改变必须重新核SHA。该抵扣不清理媒体，不降低5GiB保底，不构成下一回生产准入。
`receive_master_handoff.py`的5GiB保底按incoming真实文件系统检查，仅计本次尚未传完的母版字节；
不再把cache/final的整链临时峰值重复计入incoming。接收收据仍明确backend_started=false，
不构成后期准入；Worker开始本回时必须重新核对cache/final各自实际挂载的生产峰值预算。
2026-11-22济公传使用`cache_after_master_handoff.py prepare/run`在用户排队时冻结CacheClip（当前上传已开始时也允许补拍，但只释放此后不变的快照），等待真实`receive_master_handoff.py`的SHA/字节数/远端抽样解码收据。它转存可追溯上传收据，随后重新检查本地/远端哈希、固定目录和两次快照，再只清CacheClip内容；当前本地母片保留到最终交接阶段。

`receive_managed_finals.py --root <Windows数据根> --config <本机配置>`可在登录时隐藏启动，配置明确SSH alias、remote_root和episodes；单实例锁避免重复下载。仅接收标准post/reports报告中与当前母版一致、完整解码且混音PCM等价的成片，以SHA命名暂存断点续传，检查最终SHA/字节数/完整音频PCM/媒体/首中尾解码，按已登记回目命名。不同本地版本不覆盖。接收收据保持quality_checked=false；真实画面/字幕/同步/声音验收证据通过后，才执行现有收据门禁清理。离线、文件尚未发布、空间不足或失败都保留云端成片及恢复资料。

全托管可复用云端Codex对同SHA最终片实际完成的质量审查：`post/reports/<EP>.quality.json`必须绑定本次master/final SHA、字节数、Codex会话、画面/字幕/同步/声音四项实际检查及各自非空证据列表。Windows对证据逐一远端fresh SHA，比较审校SRT/ASS身份与混音PCM报告，并重新哈希本地完全相同成片后才写quality_checked。该证据不是无人元数据脚本生成的质量批准。原子上传`post/receipts/<EP>.json`后，按既有登记清单清理本地本集生成物；再上传真实`<EP>.windows-cleanup.json`，云端持久流程只有双方清理收据都匹配才完成。原始素材、工程、封存字幕/ASS/字体、质量证据和Windows最终片保留。


每次新约定必须同步修改AGENTS/决策文档/配置/实现；独立步骤验证后立即commit/push，另一端fetch+普通merge/pull确认HEAD。实际媒体、收据和绝对环境路径不进Git。

2026-10-26 E49新HEVC已完成上传SHA校验、本地导出母片/CacheClip内容清理和云端入队。MiSans84新母片预览已获批准，云端可在增强完成后直接烧录；交接时Worker正在等待实际性能门禁。后续阶段状态以真实作业和收据为准。

2026-11-24济公传首回真实成片质检完成后，已授权的S03E02–S03E14按回目串行推进。
每回仍独立核验真实接收母版、封存SRT/ASS、原片至母版时钟、实际母版样式预览和当前代码双卡短测。
`run_fixed_post.py`检查上一回READY及已发布的真实Codex质量证据，不以Windows下载完成阻挡下一回；
复用`kwjd-fixed-s03e02.service`，交接前持队列锁确认旧回目READY、停止空闲旧作用域，再启动新回目作用域。
不另留多个Worker循环。`jigong_quality.py --episode S03E03`等读取本回真实母版收据，首回固定SHA继续保留；
任何回目的成片清理仍须本回真实lotuspc质量收据。半小时巡检读取原托管progress的`active_episode`，
跟踪本回作业/日志/封存与下一回输入，同时保留旧回目未完成的交付收据检查。
