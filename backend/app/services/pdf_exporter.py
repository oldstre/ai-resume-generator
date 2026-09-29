"""PDF 导出服务：把数据库里的简历段内容渲染成 PDF 文件。

套壳映射：
- 原项目 render/pptx.py → 用 python-pptx 生成 PPTX
- 本模块            → 用 reportlab 生成 PDF

核心流程：
1. 注册中文字体（reportlab 默认 Helvetica 不支持中文）
2. 定义样式（标题大字号 + 正文小字号）
3. 把每段 ResumeContent.content 按 JSON 结构渲染成 Flowable
4. doc.build() → 得到 PDF 字节流
"""
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.models.resume import Resume
from app.models.resume_content import ResumeContent

# Windows 系统中文字体路径
_FONT_PATHS = {
    "SimHei": r"C:\Windows\Fonts\simhei.ttf",  # 黑体 → 标题用
    "SimSun": r"C:\Windows\Fonts\simsun.ttc",  # 宋体 → 正文用
}

# 模块级标记：字体只注册一次（重复注册 reportlab 会报错）
_fonts_registered = False

def _register_chinese_fonts() -> None:
    """注册中文字体到 reportlab。

    reportlab 默认只有 Helvetica/Times 等英文字体，
    不注册中文字体的话，所有中文会变成黑色方块。
    """
    global _fonts_registered
    if _fonts_registered:
        return
    for name, path in _FONT_PATHS.items():
        if name == "SimSun":
            # .ttc 是字体集合文件，要用 subfontIndex=0 取第一个字体
            pdfmetrics.registerFont(TTFont(name, path, subfontIndex=0))
        else:
            pdfmetrics.registerFont(TTFont(name, path))
    _fonts_registered = True

def _make_styles() -> dict[str, ParagraphStyle]:
    """创建样式集合。

    样式 = 字体 + 字号 + 颜色 + 行距的组合模板。
    定义好以后，每个 Paragraph 指定一个样式名就行——不用每次重复写参数。
    """
    return {
        # 简历大标题（最顶部，如"张三 - Python后端工程师简历"）
        "resume_title": ParagraphStyle(
            name="ResumeTitle",
            fontName="SimHei",       # 黑体
            fontSize=20,             # 20pt 大字号
            leading=28,              # 行距=字号的1.4倍，阅读舒服
            alignment=1,             # 0=左对齐 1=居中 2=右对齐
            spaceAfter=6 * mm,       # 段后留白 6mm
        ),
        # 段标题（如"教育经历""工作经历"）
        "section_title": ParagraphStyle(
            name="SectionTitle",
            fontName="SimHei",
            fontSize=14,
            leading=20,
            spaceBefore=8 * mm,      # 段前留白——和上一段拉开距离
            spaceAfter=3 * mm,
        ),
        # 正文（段内描述文字）
        "body": ParagraphStyle(
            name="Body",
            fontName="SimSun",       # 宋体
            fontSize=10.5,           # 五号字≈10.5pt，简历正文标准大小
            leading=16,
            spaceAfter=2 * mm,
        ),
        # 要点（bullet list 项，带左缩进模拟缩进效果）
        "bullet": ParagraphStyle(
            name="Bullet",
            fontName="SimSun",
            fontSize=10.5,
            leading=16,
            leftIndent=6 * mm,       # 左缩进 6mm，模拟"• 要点"的缩进
            spaceAfter=1 * mm,
        ),
    }    


def _render_section(
    content: ResumeContent,
    styles: dict[str, ParagraphStyle],
) -> list:
    """把一段 ResumeContent 渲染成 Flowable 列表。

    根据段标题和 content 的 JSON 结构，选择不同的渲染策略：
    - 含 "paragraphs" → 逐段渲染正文（自我评价段）
    - 含 "items"      → 逐条渲染条目（教育/工作/技能/项目段）
    """
    flowables = []

    # 1. 段标题（黑体，大字号）
    flowables.append(Paragraph(content.title, styles["section_title"]))

    # 2. content 可能是 None（段还没生成完）
    if content.content is None:
        flowables.append(Paragraph("（内容待生成）", styles["body"]))
        return flowables

    data = content.content

    # 3. 两种 JSON 结构：paragraphs vs items
    if "paragraphs" in data:
        # 自我评价段：paragraphs 是字符串数组
        for para in data["paragraphs"]:
            flowables.append(Paragraph(para, styles["body"]))
    elif "items" in data:
        # 其他段：items 是字典数组
        for item in data["items"]:
            flowables.extend(_render_item(item, styles))

    return flowables

def _render_item(
    item: dict,
    styles: dict[str, ParagraphStyle],
) -> list:
    """渲染一个条目（dict）成 Flowable 列表。

    条目字段分两类处理：
    - 字符串/数字 → 用 " | " 拼成一行标题（如"XX大学 | 计算机本科 | 2018-2022"）
    - 数组        → 每个元素作为一条要点（• 开头）
    """
    flowables = []

    header_parts = []   # 字符串/数字字段 → 拼标题行
    bullet_parts = []   # 数组字段 → 展开成要点

    for value in item.values():
        if isinstance(value, list):
            # 数组（如 desc[]/highlights[]/tech[]）→ 每个元素一条要点
            for element in value:
                bullet_parts.append(str(element))
        else:
            # 字符串/数字（如 school/major/years）→ 拼到标题行
            header_parts.append(str(value))

    if header_parts:
        header = " | ".join(header_parts)
        flowables.append(Paragraph(header, styles["body"]))

    for bullet in bullet_parts:
        flowables.append(Paragraph(f"• {bullet}", styles["bullet"]))

    return flowables

def export_resume_to_pdf(
    resume: Resume,
    contents: list[ResumeContent],
) -> bytes:
    """把一份简历的所有段内容渲染成 PDF，返回字节流。

    参数：
        resume: 简历主表记录（拿 title / applicant_name / target_position）
        contents: 该简历所有段内容，按 position 排序
    返回：
        PDF 文件的字节流
    """
    _register_chinese_fonts()
    styles = _make_styles()

    # BytesIO：内存里的"假文件"——PDF 先写到内存，不落盘
    # 接口直接返回字节流，不用在硬盘上存文件
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    flowables = []

    # 1. 简历大标题（最顶部）
    flowables.append(Paragraph(resume.title, styles["resume_title"]))

    # 2. 基本信息行（姓名 · 目标职位）
    info = f"{resume.applicant_name} · {resume.target_position}"
    flowables.append(Paragraph(info, styles["body"]))
    flowables.append(Spacer(1, 4 * mm))  # 标题区和正文区之间留白

    # 3. 逐段渲染——每段之间加一点留白
    for content in contents:
        flowables.append(Spacer(1, 2 * mm))
        flowables.extend(_render_section(content, styles))

    # 4. build：把所有 Flowable 排版到 PDF 页面
    doc.build(flowables)
    return buffer.getvalue()
