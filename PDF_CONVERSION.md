# PDF → Markdown 转换链路

核心入口是 `packages/markitdown/src/markitdown/converters/_pdf_converter.py`。
项目的 Python HNP／pyz 调用同一个 `PdfConverter`，不依赖 Qt、CEF、WASM 或在线服务。

## 每一步负责什么

| 业务步骤 | 实现 | 输出／规则 |
| --- | --- | --- |
| PDF 读取 | `PdfConverter.convert`、pdfplumber | 按页读取；媒体预扫描和正文处理后分别释放页缓存 |
| 正文阅读顺序 | `_baseline`、`_lines`、`_paragraphs` | 混合中英文按基线与横坐标组合；恢复段落及列表；转义原文 Markdown 特殊字符 |
| 标题／加粗 | `_render_pages`、`_line` | 依据正文相对字号和字体属性；不依据固定样本坐标 |
| 表格 | `_page_blocks`、`_table_text` | 只依据真实绘制的格线；每个字符只归属一个单元格；换行用 `<br>`，特殊符号转义 |
| 代码块 | `_code_boxes`、`_code_text` | 浅色背景＋等宽字体；连接逐行绘制的背景；保留缩进与内容；识别 JSON／Markdown／Python／JavaScript，否则 text |
| 倾斜文字水印 | `_pdf_media.rotated_text` | 按字形矩阵恢复倾斜基线，文字层直接提取；只处理 8°–82°，不把字体斜体当页面旋转 |
| 重复轮廓水印 | `media_candidates`、`vector_image`、`ocr_words` | 连接小型同色路径，对跨页重复且倾斜的轮廓单独渲染、旋正并 OCR；保留识别文字和原图；同页同文水印合并 |
| 重复 Logo／图案 | `PdfMedia.scan/page` | 图片流指纹或路径指纹跨页重复、小面积且满足位置条件；标为“原文重复图案”，不声称自动理解商标含义 |
| 正文图片 | `embedded_image`、`PdfMedia.page` | 普通 RGB／灰度嵌入图直接导出；遮罩、特殊编码和无法解码的图像按区域渲染；与 MD 同级保存，正文只写相对路径 |
| 复杂矢量图 | `PdfMedia.page` | 保留版式参考图，文字继续提取；不会凭图中文字位置猜造 Mermaid 连接关系 |
| 扫描页／轮廓化文字 | `PdfRaster`、`ocr_blocks` | PDFium 渲染 → Tesseract TSV → 文本和坐标；附 OCR 提示和原页图；空白扫描页保留页号而不进行 OCR |
| 页码 | `_render_pages` | 保留 `<!-- PDF page N -->`，供消费端按页同步；MD 预览把它显示为页尾 `--第N页--` |
| 空文档和失败 | `PdfConverter.convert` | 只有页号不算成功；解码/OCR/模型缺失明确失败；超过当前 16 MiB 预览上限明确失败 |
| 写盘 | 消费端 `huiwen_converter.api.pdf_to_md` | 先暂存图片和 MD，发布图片后原子替换 MD；失败只清理本次新增文件，不覆盖已有结果 |

## 富文本扩展协议

页首水印，JSON 字段中的换行、引号等使用标准 JSON 转义：

````markdown
<!-- PDF page 1 -->

~~~pdf-watermark
{"text":"原文水印","angle":45,"source":"text-layer"}
~~~

正文

~~~pdf-logo
{"image":"文档-p001-logo-a12f.png","alt":"原文重复图案","id":"image-..."}
~~~
````

`pdf-watermark.source` 可以为 `text-layer` 或 `outline-ocr`。OCR 可附 `confidence` 和
`image` 为原图的同目录相对路径；置信度是引擎内部估计，不能解释为人工校验的正确率。消费端应把水印放页首，
把 Logo 放在页尾页码之后。没有扩展支持的 Markdown 阅读器仍能看到 JSON 代码块，不会执行它。

## OCR 与原生运行边界

- 默认 `chi_sim`（包含拉丁字符能力），可用 `HUIWEN_OCR_LANGUAGE=eng` 选择纯英文模型。
- `HUIWEN_TESSERACT`、`HUIWEN_TESSDATA` 仅供宿主验证或显式配置；设备默认查找 Python HNP
  同前缀的 `bin/tesseract`、`share/tessdata`。
- 禁用隐式加载未随包提供的子语言，所选模型缺失会报错，不静默退成英文。
- 光栅化只调用稳定的少量 PDFium C ABI。设备加载 HNP 的 `lib/libpdfium.so`；
  宿主可指定 `HUIWEN_PDFIUM`，或使用已安装 pypdfium2 的本机二进制。
  不在鸿蒙加载 Linux wheel 的 libpdfium.so 或其整套生成绑定。
- OCR 使用本地 stdin PNM 和 stdout TSV，不生成长期临时图片、不联网；单页 OCR 超时 90 秒。
- 页面光栅化长边限制 3200 像素；可直接导出的普通嵌入图保留原始像素。MD 文本仍限制 16 MiB，图片独立保存；不改变原 PDF。

## 可复现的本地检查

宿主环境安装 `pdfplumber pypdf pypdfium2 pillow pytest reportlab` 及 Tesseract，准备中英文模型。
主项目构建业务 pyz 后运行：

```sh
HUIWEN_TESSDATA=/absolute/path/to/tessdata python tools/check_pdf_quality.py \
  /absolute/path/to/pdfs /absolute/path/to/results --bundle /absolute/path/to/huiwen_converter.pyz

PYTHONPATH=/absolute/path/to/huiwen_converter.pyz python -m pytest \
  packages/markitdown/tests/test_pdf_text_structure.py \
  packages/markitdown/tests/test_pdf_tables.py \
  packages/markitdown/tests/test_pdf_memory.py \
  packages/markitdown/tests/test_pdf_masterformat.py \
  packages/markitdown/tests/test_pdf_media.py -q
```

检查脚本保存每份文件的 MD、日志、`results.json` 和 `REPORT.md`；核对原文件 SHA-256、页数、
页码、字数覆盖、代码／表格／图片／水印／Logo 数量及 OCR 置信度。字符覆盖率是丢字检测指标，
不证明排版、阅读顺序或语义正确；OCR 准确率需要人工真值。

## 当前边界

不承诺扫描件目录、密集／合并表格、数学公式和低清文字可无损转成可编辑结构。
扫描页保留原图以便逐页核对；有文字层的多栏复杂版式仍可能存在阅读顺序问题。
水印／Logo 是保守几何启发式：图片中已经烘焙成背景且没有独立对象的斜水印，尚不能保证独立分离。
这类图片保留原貌，不能把普通 OCR 输出当作“已完整提取水印”。

## 图片文件输出契约

带图片的 PDF 调用需要传入 `pdf_image_writer(data, suffix, page_number, role)`，返回图片相对路径。
`data` 是已编码 PNG／JPEG 字节；`role` 为 image、logo、watermark、page、figure。
未提供写入器时明确报错，不退回 Base64。主项目 `_markitdown_adapter.py` 提供写入器，
`api.py` 管理同目录暂存和原子发布，核心库不依赖业务沙箱路径。

```text
output/
  文档.md
  文档-标识-p001-image-内容指纹.png
```

```markdown
![原文插图](文档-标识-p001-image-内容指纹.png)
```

文件名使用文档标识、页码、用途和内容指纹；重复转换复用相同文件，有内容冲突则报错而不覆盖。
Logo 可跨页引用首次导出的同一文件。扫描页优先复用覆盖整页的图片，避免另存一份相同的核对图。
不按通配符删除旧图片；移动／导出文档时需要携带所引用的图片文件。已有的旧 Base64 MD 不自动迁移，重新转换会生成路径版本。
检查脚本同时验证每个图片引用均在 MD 同目录、文件存在且可解码，并要求新 MD 不含内嵌图片数据。
