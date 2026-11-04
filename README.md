# MarkItDown

## 汇文 PDF → Markdown 格式对照

本节描述本 fork 当前 PDF 转换器实际生成的字符和结构，不代表其他文件格式的转换规则。
转换核心负责提取内容和生成 Markdown；消费端负责图片写盘、预览样式和分页显示。
实现入口为 `packages/markitdown/src/markitdown/converters/_pdf_converter.py`，媒体与 OCR
在同目录的 `_pdf_media.py`、`_pdf_raster.py`。完整链路、接口与本地验证见 [PDF_CONVERSION.md](PDF_CONVERSION.md)。

### 自动转换规则

| PDF 中遇到的情况 | 输出字符／格式 | 消费端预览效果与限制 |
| --- | --- | --- |
| 普通文字和段落 | 普通文字；段落间空一行 | 正文段落 |
| 同一段因版面宽度换行 | 合并行；英文词之间保留必要空格 | 减少版面换行造成的断句；复杂多栏仍需核对顺序 |
| 字号明显大于正文、符合长度和结尾标点条件 | `# 标题` 至 `###### 标题` | 一至六级标题；基于字号等启发式，不是语义识别；“目的”“重要规则”另按三级标题处理 |
| 字体名称带 Bold、Black、Heavy、Demi 特征 | `**文字**` | 加粗文字 |
| `•`、`●`、`▪`、`◦` 项目符号 | `- 内容` | 无序列表 |
| 原文数字编号 | 保留编号并分段，例如 `1. 内容`、`1、内容` | 标准 Markdown 编号可渲染为有序列表；不统一改写所有编号 |
| 有真实绘制格线的表格 | 管道符分列，第一行后插入 `---` 分隔行 | Markdown 表格；首行作为 Markdown 表头，不代表已理解原表头语义；不猜测无框线表格 |
| 表格单元格内换行 | `<br>` | 单元格内换行 |
| 浅灰背景、等宽字体等满足代码区域规则 | 反引号代码围栏，附 `python`、`javascript`、`json`、`markdown` 或 `text` | 保留代码缩进与空行；语言判断为启发式；无法判断使用 `text` |
| 页面底部字号较小的文字 | `<small>页脚文字</small>` | 小字号页脚 |
| 可直接解码的普通 RGB／灰度嵌入图 | `![原文插图](图片文件名.jpg)` 或 `.png` | 同目录图片；尽量直接导出 |
| 带遮罩、特殊编码或不能直接解码的嵌入图 | 对应区域渲染为图片，再输出普通图片语法 | 保留外观，不凭图像猜造结构 |
| 符合复杂矢量图保留条件的页面 | `![原页图示（布局核对）](图片文件名.png)` | 保留版式参考图；不自动生成 Mermaid |
| 无可用文字层的扫描页／轮廓化文字页 | OCR 普通文字、以 `> ` 开头的核对提示、原页图片 | 供人工核对；不保证表格、公式或字号语义恢复；空白扫描页跳过 OCR |
| 原 PDF 页边界 | `<!-- PDF page N -->` | 保留源页标记；汇文预览在该页末尾显示 `--第N页--` 并用于滚动同步 |
| 倾斜文字层水印 | `pdf-watermark` JSON 围栏，`source` 为 `text-layer` | 提取倾斜文字层；消费端在页首显示；字体斜体不等于页面文字旋转 |
| 符合条件的跨页重复倾斜轮廓水印 | 单独渲染、旋正并 OCR，输出 `pdf-watermark`；附原图路径、角度和置信度 | `source` 为 `outline-ocr`；OCR 未达条件时保留为重复图案，不宣称已识别水印文字 |
| 符合重复、面积和位置等条件的 Logo／图案 | `pdf-logo` JSON 围栏 | 汇文预览放在页码之后；只识别重复图案，不理解商标含义 |

表格字符示例：

```markdown
| 名称 | 说明 |
| --- | --- |
| 项目一 | 第一行<br>第二行 |
```

代码字符示例：

````markdown
```python
def hello():
    print("你好")
```
````

### 页码、水印、Logo 和图片协议

页码标记位于源页内容之前；“显示在页尾”是消费端布局行为，不会将 MD 中的标记改成页尾文字。
水印、Logo 的 JSON 使用标准转义。普通 Markdown 阅读器可将扩展显示为代码框，汇文预览按类型渲染：

````markdown
<!-- PDF page 1 -->

~~~pdf-watermark
{"text":"内部资料","angle":45,"source":"text-layer"}
~~~

正文。

![原文插图](文档-p001-image-标识.jpg)

~~~pdf-logo
{"image":"文档-p001-logo-标识.png","alt":"原文重复图案","id":"图案标识"}
~~~
````

OCR 水印可输出：

````markdown
~~~pdf-watermark
{"text":"内部资料","angle":45,"source":"outline-ocr","confidence":85.2,"image":"文档-p001-watermark-标识.png"}
~~~
````

`confidence` 是引擎估计值，不是人工正确率。图片与 MD 保存在同一个输出目录，Markdown
只写相对路径；核心通过 `pdf_image_writer` 回调交付图片，消费端管理暂存、发布和失败清理。
图片丢失／损坏后的破裂占位属于预览行为，不修改 Markdown 原文。移动文档时需要携带引用图片。

### 预览支持不等于 PDF 会自动生成

MD 预览器可以解析以下字符，但本转换器尚不自动恢复相应 PDF 语义：

| Markdown 字符 | 预览能力 | PDF 当前转换边界 |
| --- | --- | --- |
| `*文字*`、`~~文字~~` | 斜体、删除线 | 不根据 PDF 斜体字形或删除线自动生成 |
| 单反引号包裹文字 | 行内代码 | 不自动区分正文中的行内代码 |
| `> 内容` | 引用块 | 当前用于 OCR 提示，不自动判定普通原文是否为引用 |
| `[名称](https://example.com)` | 超链接 | 不恢复 PDF 链接注释；预览可能自动识别普通网址 |
| `- [ ] 待办`、`- [x] 完成` | 任务列表 | 不自动识别 PDF 复选框 |
| `[^1]` 与 `[^1]: 注释` | 脚注与跳转 | 不自动建立 PDF 脚注引用关系 |
| `==文字==`、`H~2~O`、`x^2^` | 高亮、下标、上标 | 不根据 PDF 高亮或字形位置自动生成 |
| 术语下一行以 `: ` 开头 | 定义列表 | 不自动识别术语与定义关系 |
| `$公式$`、`$$公式$$`、`\(...\)`、`\[...\]`、`math`／`latex` 围栏 | 数学公式 | 不自动将 PDF 公式还原为 LaTeX |
| `mermaid` 围栏 | 流程图、时序图等 | 不从 PDF 图形反推节点与连接，保留图片供核对 |

### 原文字面量与能力边界

普通 PDF 正文中的 Markdown 特殊字符会转义，以免金额、数组、标签等被误当成富文本。
例如 `*` → `\*`、`$` → `\$`、`#` → `\#`、`[`／`]` → `&#91;`／`&#93;`，
`&` → `&amp;`；代码区域则保留代码文本。原文恰好印有 LaTeX 或 Mermaid 字符，不代表转换器
会主动将其解释为公式或流程图。

转换优先保留原文，不保证复杂多栏、合并表格、扫描公式和低清文字无损还原。
已烘焙进图片背景的斜水印不保证独立提取；保留原图不能等同于已完成水印分离。

[![PyPI](https://img.shields.io/pypi/v/markitdown.svg)](https://pypi.org/project/markitdown/)
![PyPI - Downloads](https://img.shields.io/pypi/dd/markitdown)

> [!IMPORTANT]
> MarkItDown performs I/O with the privileges of the current process. Like open() or requests.get(), it will access resources that the process itself can access. Sanitize your inputs in untrusted environments, and call the narrowest `convert_*` function needed for your use case (e.g., `convert_stream()`, or `convert_local()`). See the [Security Considerations](#security-considerations) section of the documentation for more information.

MarkItDown is a lightweight Python utility for converting various files to Markdown for use with LLMs and related text analysis pipelines. To this end, it is most comparable to [textract](https://github.com/deanmalmgren/textract), but with a focus on preserving important document structure and content as Markdown (including: headings, lists, tables, links, etc.) While the output is often reasonably presentable and human-friendly, it is meant to be consumed by text analysis tools -- and may not be the best option for high-fidelity document conversions for human consumption.

MarkItDown currently supports the conversion from:

- PDF
- PowerPoint
- Word
- Excel
- Images (EXIF metadata and OCR)
- Audio (EXIF metadata and speech transcription)
- HTML
- Text-based formats (CSV, JSON, XML)
- ZIP files (iterates over contents)
- YouTube URLs
- EPubs
- ... and more!

## Why Markdown?

Markdown is extremely close to plain text, with minimal markup or formatting, but still
provides a way to represent important document structure. Mainstream LLMs, such as
OpenAI's GPT-4o, natively "_speak_" Markdown, and often incorporate Markdown into their
responses unprompted. This suggests that they have been trained on vast amounts of
Markdown-formatted text, and understand it well. As a side benefit, Markdown conventions
are also highly token-efficient.

## Prerequisites
MarkItDown requires Python 3.10 through 3.14. It is recommended to use a virtual environment to avoid dependency conflicts.

With the standard Python installation, you can create and activate a virtual environment using the following commands:

```bash
python -m venv .venv
source .venv/bin/activate
```

If using `uv`, you can create a virtual environment with:

```bash
uv venv --python=3.12 .venv
source .venv/bin/activate
# NOTE: Be sure to use 'uv pip install' rather than just 'pip install' to install packages in this virtual environment
```

If you are using Anaconda, you can create a virtual environment with:

```bash
conda create -n markitdown python=3.12
conda activate markitdown
```

## Installation

To install MarkItDown, use pip: `pip install 'markitdown[all]'`. Alternatively, you can install it from the source:

```bash
git clone git@github.com:microsoft/markitdown.git
cd markitdown
pip install -e 'packages/markitdown[all]'
```

## Usage

### Command-Line

```bash
markitdown path-to-file.pdf > document.md
```

Or use `-o` to specify the output file:

```bash
markitdown path-to-file.pdf -o document.md
```

You can also pipe content:

```bash
cat path-to-file.pdf | markitdown
```

### Optional Dependencies
MarkItDown has optional dependencies for activating various file formats. Earlier in this document, we installed all optional dependencies with the `[all]` option. However, you can also install them individually for more control. For example:

```bash
pip install 'markitdown[pdf, docx, pptx]'
```

will install only the dependencies for PDF, DOCX, and PPTX files.

At the moment, the following optional dependencies are available:

* `[all]` Installs all optional dependencies
* `[pptx]` Installs dependencies for PowerPoint files
* `[docx]` Installs dependencies for Word files
* `[xlsx]` Installs dependencies for Excel files
* `[xls]` Installs dependencies for older Excel files
* `[pdf]` Installs dependencies for PDF files
* `[outlook]` Installs dependencies for Outlook messages
* `[az-doc-intel]` Installs dependencies for Azure Document Intelligence
* `[az-content-understanding]` Installs dependencies for Azure Content Understanding
* `[audio-transcription]` Installs dependencies for audio transcription of wav and mp3 files
* `[youtube-transcription]` Installs dependencies for fetching YouTube video transcription

### Plugins

MarkItDown also supports 3rd-party plugins. Plugins are disabled by default. To list installed plugins:

```bash
markitdown --list-plugins
```

To enable plugins use:

```bash
markitdown --use-plugins path-to-file.pdf
```

To find available plugins, search GitHub for the hashtag `#markitdown-plugin`. To develop a plugin, see `packages/markitdown-sample-plugin`.

#### markitdown-ocr Plugin

The `markitdown-ocr` plugin adds OCR support to PDF, DOCX, PPTX, and XLSX converters, extracting text from embedded images using LLM Vision — the same `llm_client` / `llm_model` pattern that MarkItDown already uses for image descriptions. No new ML libraries or binary dependencies required.

**Installation:**

```bash
pip install markitdown-ocr
pip install openai  # or any OpenAI-compatible client
```

**Usage:**

Pass the same `llm_client` and `llm_model` you would use for image descriptions:

```python
from markitdown import MarkItDown
from openai import OpenAI

md = MarkItDown(
    enable_plugins=True,
    llm_client=OpenAI(),
    llm_model="gpt-4o",
)
result = md.convert("document_with_images.pdf")
print(result.markdown)
```

If no `llm_client` is provided the plugin still loads, but OCR is silently skipped and the standard built-in converter is used instead.

See [`packages/markitdown-ocr/README.md`](packages/markitdown-ocr/README.md) for detailed documentation.

### Azure Content Understanding

[Azure Content Understanding](https://learn.microsoft.com/azure/ai-services/content-understanding/) provides higher-quality conversion with structured field extraction (YAML front matter), multi-modal support (documents, images, audio, video), and configurable analyzers.

Install: `pip install 'markitdown[az-content-understanding]'`

#### When to use Content Understanding

Content Understanding is ideal when you need capabilities beyond what built-in or Document Intelligence converters provide:

- **Audio and video files** — CU is the only option for video, and the higher-quality cloud option for audio. Built-in converters have no video support and only basic audio transcription.
- **Structured field extraction** — [Prebuilt](https://learn.microsoft.com/azure/ai-services/content-understanding/concepts/prebuilt-analyzers) or [custom-built](https://learn.microsoft.com/azure/ai-services/content-understanding/how-to/customize-analyzer-content-understanding-studio?tabs=portal) analyzers extract domain-specific fields (invoice amounts, receipt dates, contract clauses) serialized as YAML front matter. Neither built-in nor Doc Intel integration exposes fields.
- **Higher-quality document extraction** — Cloud-based layout analysis and OCR for scanned PDFs, complex tables, and multi-page documents.
- **Single API for all modalities** — One `cu_endpoint` handles documents, images, audio, and video with automatic analyzer routing.

| Capability | Built-in converters | Azure Document Intelligence | Azure Content Understanding |
|------------|---------------------|-----------------------------|-----------------------------|
| Document conversion | Offline, format-specific extraction | Cloud layout extraction | Cloud multimodal extraction |
| Structured fields | Not available | Not exposed by this integration | YAML front matter from analyzer fields |
| Custom analyzers | Not available | Not configurable in this integration | Supported with `cu_analyzer_id` |
| Audio and video | Basic audio, no video | Not supported | Audio and video analyzers |
| Cost | Local compute only | Billable Azure API calls | Billable Azure API calls |

**CLI:**

```bash
markitdown path-to-file.pdf --use-cu --cu-endpoint "<content_understanding_endpoint>"
```

The endpoint can also be set once in the environment, so callers only need `--use-cu`:

```bash
export MARKITDOWN_CU_ENDPOINT="<content_understanding_endpoint>"
markitdown path-to-file.pdf --use-cu
```

**Python API:**

```python
from markitdown import MarkItDown

# Zero-config — auto-selects analyzer per file type
md = MarkItDown(cu_endpoint="<content_understanding_endpoint>")
result = md.convert("report.pdf")   # documents → prebuilt-documentSearch
result = md.convert("meeting.mp4")  # video → prebuilt-videoSearch
result = md.convert("call.wav")     # audio → prebuilt-audioSearch
print(result.markdown)
```

**With a custom analyzer** (for domain-specific field extraction):

```python
md = MarkItDown(
    cu_endpoint="<content_understanding_endpoint>",
    cu_analyzer_id="my-invoice-analyzer",
)
result = md.convert("invoice.pdf")
print(result.markdown)
# Output includes YAML front matter with extracted fields:
# ---
# contentType: document
# fields:
#   VendorName: CONTOSO LTD.
#   InvoiceDate: '2019-11-15'
# ---
# <!-- page 1 -->
# ...
```

When `cu_analyzer_id` is set, the converter automatically scopes it to compatible file types based on the analyzer's modality. Incompatible types (e.g., audio files with a document analyzer) auto-route to default prebuilt analyzers.

**Cost note:** Each `convert()` call for a CU-routed format is a billable Azure API call. Use `cu_file_types` to restrict which formats route to CU:

```python
from markitdown.converters import ContentUnderstandingFileType

md = MarkItDown(
    cu_endpoint="<content_understanding_endpoint>",
    cu_file_types=[ContentUnderstandingFileType.PDF],  # only PDFs use CU
)
```

More information about Azure Content Understanding can be found [here](https://learn.microsoft.com/azure/ai-services/content-understanding/).

### Azure Document Intelligence

To use Microsoft Document Intelligence for conversion:

```bash
markitdown path-to-file.pdf -o document.md -d -e "<document_intelligence_endpoint>"
```

The endpoint can also be set once in the environment, so callers only need `-d`:

```bash
export MARKITDOWN_DOCINTEL_ENDPOINT="<document_intelligence_endpoint>"
markitdown path-to-file.pdf -o document.md -d
```

More information about how to set up an Azure Document Intelligence Resource can be found [here](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/how-to-guides/create-document-intelligence-resource?view=doc-intel-4.0.0)

### Python API

Basic usage in Python:

```python
from markitdown import MarkItDown

md = MarkItDown(enable_plugins=False) # Set to True to enable plugins
result = md.convert("test.xlsx")
print(result.markdown)
```

Document Intelligence conversion in Python:

```python
from markitdown import MarkItDown

md = MarkItDown(docintel_endpoint="<document_intelligence_endpoint>")
result = md.convert("test.pdf")
print(result.markdown)
```

To use Large Language Models for image descriptions (currently only for pptx and image files), provide `llm_client` and `llm_model`:

```python
from markitdown import MarkItDown
from openai import OpenAI

client = OpenAI(max_retries=5)
md = MarkItDown(llm_client=client, llm_model="gpt-4o", llm_prompt="optional custom prompt")
result = md.convert("example.jpg")
print(result.markdown)
```

`max_retries` controls the OpenAI client's automatic retries for retryable errors (the default is 2); `5` allows up to six attempts with backoff. See the [OpenAI SDK retry documentation](https://github.com/openai/openai-python#retries).

If any attempt succeeds, image conversion continues normally. If the client raises an error after exhausting its retries, or encounters a non-retryable error, MarkItDown tries other applicable converters and raises `FileConversionException` only if none succeeds.

### Docker

```sh
docker build -t markitdown:latest .
docker run --rm -i markitdown:latest < ~/your-file.pdf > output.md
```

## Contributing

Before starting significant work, please read [What to Contribute](#what-to-contribute), which describes what is in and out of scope for this repository.

This project welcomes contributions and suggestions. Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit https://cla.opensource.microsoft.com.

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

### What to Contribute

MarkItDown is a Python utility for converting files to Markdown for use with LLMs and related text analysis pipelines. This repository is intended to provide Python libraries that can be incorporated into other systems — not the end-user applications built on top of them.

#### In scope

- Improvements to the fidelity of existing converters (New formats are added sparingly -- especially if they incur new dependencies. In most cases, new formats can be better supported via [3rd-party plugins](#extending-markitdown-without-changing-this-repository).)
- Bug fixes, performance improvements, and security fixes
- The `markitdown` command-line interface
- The `markitdown-mcp` package
- Tests, documentation, and developer tooling

#### Out of scope

We cannot accept additional applications, services, or servers. This includes:

- Web servers, REST or HTTP APIs, and hosted conversion services
- Web frontends and browser-based user interfaces
- Desktop and mobile applications (PyQt, PySide, Tkinter, Electron, Flutter, and similar)

Projects like these are genuinely useful, and we would rather see them thrive than be turned away. If you are interested in providing a web service, API, or graphical application for MarkItDown, please maintain it as a separate package or project that depends on [`markitdown` from PyPI](https://pypi.org/project/markitdown/).

### Extending MarkItDown Without Changing This Repository

MarkItDown supports 3rd-party plugins, so support for a new format can be published and installed independently of this repository:

```sh
markitdown --list-plugins
markitdown --use-plugins path-to-file.pdf
```

See `packages/markitdown-sample-plugin` to get started, and tag your repository `#markitdown-plugin` so that others can find it.

### How to Contribute

You can help by looking at issues or helping review PRs. We have also marked some issues as 'open for contribution' and PRs as 'open for reviewing' to help facilitate community contributions. These labels are suggestions; contributions within the scope described above are welcome.

<div align="center">

|            | All                                                          | Especially Needs Help from Community                                                                                                      |
| ---------- | ------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| **Issues** | [All Issues](https://github.com/microsoft/markitdown/issues) | [Issues open for contribution](https://github.com/microsoft/markitdown/issues?q=is%3Aissue+is%3Aopen+label%3A%22open+for+contribution%22) |
| **PRs**    | [All PRs](https://github.com/microsoft/markitdown/pulls)     | [PRs open for reviewing](https://github.com/microsoft/markitdown/pulls?q=is%3Apr+is%3Aopen+label%3A%22open+for+reviewing%22)              |

</div>

### Running Tests and Checks

- Navigate to the MarkItDown package:

  ```sh
  cd packages/markitdown
  ```

- Install `hatch` in your environment and run tests:

  ```sh
  pip install hatch  # Other ways of installing hatch: https://hatch.pypa.io/dev/install/
  hatch shell
  hatch test
  ```

  (Alternative) Use the Devcontainer which has all the dependencies installed:

  ```sh
  # Reopen the project in Devcontainer and run:
  hatch test
  ```

- Run pre-commit checks before submitting a PR: `pre-commit run --all-files`

### Security Considerations

MarkItDown performs I/O with the privileges of the current process. Like `open()` or `requests.get()`, it will access resources that the process itself can access.

**Sanitize your inputs:** Do not pass untrusted input directly to MarkItDown. If any part of the input may be controlled by an untrusted user or system, such as in hosted or server-side applications, it must be validated and restricted before calling MarkItDown. Depending on your environment, this may include restricting file paths, limiting URI schemes and network destinations, and blocking access to private, loopback, link-local, or metadata-service addresses.

**Call only the conversion method you need:** Prefer the narrowest conversion API that fits your use case. MarkItDown's `convert()` method is intentionally permissive and can handle local files, remote URIs, and byte streams. If your application only needs to read local files, call `convert_local()` instead. If you need more control over URI fetching, call `requests.get()` yourself and pass the response object to `convert_response()`. For maximum control, open a stream to the input you want converted and call `convert_stream()`.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
