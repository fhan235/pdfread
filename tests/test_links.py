"""文内链接: 提取、坐标换算与接口。"""

from pathlib import Path

import pymupdf
import pytest

from pdfread.pdfworker import page_links


def _stamp(path):
    st = Path(path).stat()
    return (st.st_mtime_ns, st.st_size)


def _paper(path, rotation=0):
    """两页: 第 1 页有显式目标、命名目标(LaTeX 风格)、外链各一个;
    第 2 页在 y=300(显示坐标, 未旋转时)处有目标文字。"""
    doc = pymupdf.open()
    doc.new_page(width=600, height=800)
    doc.new_page(width=600, height=800)
    doc[1].insert_text((72, 300), "TARGET")
    target = doc[1].xref
    # 显式目标: /Dest [页 /XYZ x y], PDF 原始坐标 y = 800 - 290
    goto = doc.get_new_xref()
    doc.update_object(goto, "<</Type/Annot/Subtype/Link/Rect[100 685 140 700]"
                            f"/Border[0 0 0]/Dest[{target} 0 R /XYZ 72 510 0]>>")
    # 命名目标: /Dest (cite.x), 由目录 /Names /Dests 解析
    named = doc.get_new_xref()
    doc.update_object(named, "<</Type/Annot/Subtype/Link/Rect[200 685 240 700]"
                             "/Border[0 0 0]/Dest(cite.x)>>")
    uri = doc.get_new_xref()
    doc.update_object(uri, "<</Type/Annot/Subtype/Link/Rect[300 685 340 700]/Border[0 0 0]"
                           "/A<</S/URI/URI(https://example.com)>>>>")
    doc.xref_set_key(doc[0].xref, "Annots", f"[{goto} 0 R {named} 0 R {uri} 0 R]")
    dests = doc.get_new_xref()
    doc.update_object(dests, f"<</Names[(cite.x)[{target} 0 R /XYZ 72 510 0]]>>")
    doc.xref_set_key(doc.pdf_catalog(), "Names", f"<</Dests {dests} 0 R>>")
    for p in doc:
        p.set_rotation(rotation)
    doc.save(path)
    doc.close()
    return path


def test_extracts_internal_links_only(tmp_path):
    path = str(_paper(tmp_path / "p.pdf"))
    links = page_links(path, 1, _stamp(path))
    assert len(links) == 2   # 外链被忽略
    for x0, y0, x1, y1, page, y, view in links:
        assert page == 2
        assert y == pytest.approx(290, abs=0.5)   # 两类目标都换算成显示坐标
        assert view == y                          # 不是图注, 滚动位置即锚点
        assert 100 <= y0 < y1 <= 116 and x0 < x1


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_named_and_explicit_targets_agree_on_rotated_pages(tmp_path, rotation):
    path = str(_paper(tmp_path / "r.pdf", rotation))
    links = page_links(path, 1, _stamp(path))
    with pymupdf.open(path) as doc:
        expected = (pymupdf.Point(72, 290) * doc[1].rotation_matrix).y
        w, h = doc[0].rect.width, doc[0].rect.height
    assert len(links) == 2
    for x0, y0, x1, y1, page, y, view in links:
        assert y == pytest.approx(expected, abs=0.5)
        assert view == y
        assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h   # 热区在旋转后的页面内


def _floats(path):
    """第 2 页: 图 1(位图, 图注在下) / 表 1(图注在上, 横线) / 图 2(矢量, 紧贴表格下方)
    / 双栏(左栏正文、右栏图 3) / 普通段落上方有图。第 1 页放指向各锚点的链接。"""
    doc = pymupdf.open()
    doc.new_page(width=600, height=800)
    pg = doc.new_page(width=600, height=800)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), 0)
    pix.clear_with(180)

    def text(x, baseline, s):
        pg.insert_text((x, baseline), s, fontsize=9)
        return (x + 1, baseline - 9)   # 锚点: 行顶

    def image(rect):
        pg.insert_image(rect, pixmap=pix, keep_proportion=False)   # 铺满给定区域

    image(pymupdf.Rect(100, 60, 500, 200))
    a_fig1 = text(100, 215, "Figure 1: Overview of the model.")
    a_tab1 = text(100, 245, "Table 1: Main results.")
    for y in (252, 270, 288):   # 表格横线 + 行
        pg.draw_line((100, y), (500, y), width=0.5)
    text(110, 264, "Model  Score")
    pg.draw_rect(pymupdf.Rect(100, 296, 500, 400), color=(0, 0, 0), fill=(0.8, 0.8, 1))
    a_fig2 = text(100, 415, "Fig. 2: Second figure.")
    # 离图 2 只有 30pt(在吸附距离内), 中间隔着图 2 的窄图注, 不得合并
    image(pymupdf.Rect(320, 430, 500, 540))
    text(60, 563, "the left column continues with body text")
    a_fig3 = text(320, 556, "Figure 3: Right column figure.")
    image(pymupdf.Rect(100, 600, 500, 680))
    a_body = text(100, 700, "3 Method")

    anchors = [a_fig1, a_tab1, a_fig2, a_fig3, a_body]
    for i, (x, y) in enumerate(anchors):
        doc[0].insert_link({"kind": pymupdf.LINK_GOTO, "page": 1,
                            "from": pymupdf.Rect(50 + i * 60, 100, 90 + i * 60, 112),
                            "to": pymupdf.Point(x, y)})
    doc.save(path)
    doc.close()
    return path


def test_figure_links_scroll_to_figure_top(tmp_path):
    path = str(_floats(tmp_path / "f.pdf"))
    links = sorted(page_links(path, 1, _stamp(path)), key=lambda l: l[0])
    fig1, tab1, fig2, fig3, body = [(l[5], l[6]) for l in links]
    assert fig1[1] == pytest.approx(60, abs=1)     # 图注在下: 滚到图顶
    assert tab1[1] == tab1[0]                      # 表格图注在上: 落点不变
    assert fig2[1] == pytest.approx(296, abs=1)    # 不吞并上方表格的横线
    assert fig3[1] == pytest.approx(430, abs=1)    # 双栏按锚点 x 认出右栏图注; 不并入图 2
    assert body[1] == body[0]                      # 不是图注: 即使上方有图也不动
    for anchor, view in (fig1, fig2, fig3):
        assert view < anchor                       # 锚点仍是图注, 供高亮


def test_side_by_side_figures_use_their_own_caption(tmp_path):
    """双栏并排的两张图, 图注在同一行且只隔 14pt(ViT 论文的实际版式);
    标签用粗体(PyMuPDF 会把它和正文切成两个"行"), 锚点在标签之后(hyperref)。"""
    doc = pymupdf.open()
    doc.new_page(width=600, height=800)
    pg = doc.new_page(width=600, height=800)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), 0)
    pix.clear_with(180)
    # 左图矮、右图高: 若左图注的宽度越过右栏标签, 会被右图带到更高处
    left_body = "Short figure on the left with a long caption"
    lx = 60 + pymupdf.get_text_length("Figure 4: ", fontname="hebo", fontsize=9)
    rx = lx + pymupdf.get_text_length(left_body, fontsize=9) + 14
    pg.insert_image(pymupdf.Rect(60, 150, rx - 20, 300), pixmap=pix, keep_proportion=False)
    pg.insert_image(pymupdf.Rect(rx, 60, 560, 300), pixmap=pix, keep_proportion=False)
    anchors = []
    for x0, body, num in ((60, left_body, 4), (rx, "Tall one.", 5)):
        label = f"Figure {num}: "
        pg.insert_text((x0, 315), label, fontname="hebo", fontsize=9)
        bx = x0 + pymupdf.get_text_length(label, fontname="hebo", fontsize=9)
        pg.insert_text((bx, 315), body, fontsize=9)
        anchors.append((bx, 306))
    for i, (x, y) in enumerate(anchors):
        doc[0].insert_link({"kind": pymupdf.LINK_GOTO, "page": 1,
                            "from": pymupdf.Rect(50 + i * 60, 100, 90 + i * 60, 112),
                            "to": pymupdf.Point(x, y)})
    path = str(tmp_path / "side.pdf")
    doc.save(path)
    doc.close()
    left, right = sorted(page_links(path, 1, _stamp(path)), key=lambda l: l[0])
    assert left[6] == pytest.approx(150, abs=1)    # 不被右边更高的图带上去
    assert right[6] == pytest.approx(60, abs=1)


def test_rejects_modified_file(tmp_path):
    path = str(_paper(tmp_path / "m.pdf"))
    with pytest.raises(ValueError):
        page_links(path, 1, (0, 0))


def test_links_api(client, tmp_path):
    path = _paper(tmp_path / "api.pdf")
    info = client.post("/api/open", params={"path": str(path)}).json()
    r = client.get("/api/links/1", params={"doc": info["doc"]})
    assert r.status_code == 200
    assert [l[4] for l in r.json()["links"]] == [2, 2]
    assert all(len(l) == 7 for l in r.json()["links"])
    assert client.get("/api/links/2", params={"doc": info["doc"]}).json()["links"] == []
    assert client.get("/api/links/9", params={"doc": info["doc"]}).status_code == 404


def test_links_api_rejects_non_pdf(client, tmp_path):
    txt = tmp_path / "book.txt"
    txt.write_text("第一章 开始\n\n这是正文。\n", "utf-8")
    info = client.post("/api/open", params={"path": str(txt)}).json()
    assert client.get("/api/links/1", params={"doc": info["doc"]}).status_code == 400
