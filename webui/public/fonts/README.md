# 思源黑体 / Source Han Sans

界面使用 Adobe 官方的思源黑体简体中文可变字体，中英文共用该字体。代码保留等宽字体，中文字符回退到思源黑体。字体由 Jelly 本地提供，不依赖 CDN 或用户预先安装字体。

- 上游：[adobe-fonts/source-han-sans](https://github.com/adobe-fonts/source-han-sans)
- 固定版本：`a4f7cf94edfb9d7ffbdfc4841de276358bd7e0f2`（2.005）
- 原文件：[Variable/WOFF2/TTF/SourceHanSansSC-VF.ttf.woff2](https://github.com/adobe-fonts/source-han-sans/blob/a4f7cf94edfb9d7ffbdfc4841de276358bd7e0f2/Variable/WOFF2/TTF/SourceHanSansSC-VF.ttf.woff2)
- 本地文件：`SourceHanSansSC-VF.woff2`，仅简化文件名，字体内容未经修改。
- SHA-256：`cfec773cdc2ea964de8713471c6fd20774bc40617f5567f92efeeccaca6604b0`
- 许可证：随附 `SourceHanSans-LICENSE.txt`（SIL Open Font License 1.1）。

保留官方完整字形及可变字重，以支持 Agent 动态生成的中文内容。页面预加载 WOFF2，并以 `font-display: swap` 避免阻塞文字显示。
