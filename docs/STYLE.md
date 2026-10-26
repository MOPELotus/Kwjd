# 可复用MiSans样式

用户已委托Windows Codex制作样式、提取字体和PotPlayer实画面预览，Aegisub可选。

- styles/template.ass：1920x1080、Default样式，不含某集对白。
- 已安装MiSans VF的Demibold命名实例为wght450；提取静态MiSans Subtitle并放styles/fonts/MiSans-Subtitle-Demibold.ttf，保留字体许可/来源元数据，不进Git。不要误用常见600权重。
- 当前候选84号、白字、深色描边3.5、阴影1、底部居中、左右90/底54边距。52太小、72仍小，用户要求84；位置等新E49画面再调，不算已批准。
- prepare_subtitle_style.py生成字体、模板、实画面图；preview_subtitle_style.py --variant size84生成对应36秒1080p25预览，用PotPlayer播放最终libass渲染，避免播放器覆盖样式。检查长短/双行/明暗背景，不只看静态设置。
- kwjd.py make-ass保留SRT内容/空格/时间（四舍五入到ASS10ms），样式修改不改封存SRT。
- sync-style只同步当前模板、字体、可选批准sidecar，不上传预览媒体/旧样式；双端SHA及Linux fc-match/libass实际fontselect均检查。
- 最终样式/位置确认后生成template.ass.ready.json，approved=true且sha256匹配；模板改动旧批准失效。Worker等匹配批准再烧录，独立增强不受影响。
- 残留硬字幕已接受，不为遮盖残片加黑条或改对白；ASS在增强后烧录。
