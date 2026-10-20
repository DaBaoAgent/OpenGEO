# -*- coding: utf-8 -*-
"""Build a publish package: out/<N> <中文标题>/ with docx + html + 图片/.

Usage:
    python scripts/build_publish_package.py --draft drafts/03-x --out-name "2 昆山租轮椅体验" \
        --image-map "hero.jpg=01-封面.jpg;02-a.jpg=02-工厂取车.jpg;..."

The docx comes from the markdown through a document-style HTML (SimSun 12pt,
2em indent, h1-h3, figcaptions) and the local editor SDK converter.
"""
from __future__ import annotations

import argparse, base64, json, re, shutil, subprocess, sys, tempfile
from pathlib import Path

import markdown
from PIL import Image

PLUGIN = Path(r"C:\Users\Administrator\.workbuddy-ai\plugins\cache\workbuddy-builtin\workbuddy-docx"
              r"\5.8.4-wb.41366630.g73952edb.h4da087921e4a")
CONVERT = PLUGIN / "skills" / "html-to-doc" / "scripts" / "convert_doc.py"

CSS = """
@page { @bottom-center { content: "第 " counter(page) " 页"; } }
body { font-family: "SimSun","宋体",serif; font-size: 12pt; line-height: 1.75; color: #1a1a1a; }
p { text-indent: 2em; margin: 0 0 9pt 0; text-align: justify; }
h1 { font-family: "Microsoft YaHei","微软雅黑",sans-serif; font-size: 21pt; line-height: 1.35;
     color: #0d273e; margin: 0 0 10pt 0; text-indent: 0; }
h2 { font-family: "Microsoft YaHei","微软雅黑",sans-serif; font-size: 15pt; color: #0d273e;
     border-left: 5px solid #f5a623; padding-left: 8pt; margin: 20pt 0 8pt 0; text-indent: 0; }
h3 { font-family: "Microsoft YaHei","微软雅黑",sans-serif; font-size: 12.5pt; color: #1f3b55;
     margin: 13pt 0 5pt 0; text-indent: 0; }
ul, ol { margin: 0 0 9pt 0; padding-left: 20pt; }
li { margin: 0 0 4pt 0; text-align: justify; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0 12pt 0; font-size: 10.5pt; }
th, td { border: 1px solid #c3ced9; padding: 5pt 7pt; text-align: left; vertical-align: top; }
th { background-color: #eef3f7; font-weight: bold; }
figure { margin: 12pt 0; text-align: center; text-indent: 0; }
figure img { display: block; margin: 0 auto; }
figcaption { font-size: 9.5pt; color: #6b7a89; text-align: center; margin-top: 4pt; text-indent: 0; }
blockquote { margin: 10pt 0; padding: 8pt 10pt; background-color: #f4f7fa;
             border-left: 3px solid #9fb2c4; font-size: 10.5pt; }
blockquote p { text-indent: 0; margin: 0; }
p.cap { text-align: center; font-size: 9.5pt; color: #6b7a89; text-indent: 0; margin: 0 0 10pt 0; }
"""


def build_doc_html(md_path: Path, workdir: Path, title: str) -> Path:
    raw = md_path.read_text(encoding="utf-8")
    m = re.match(r"^---\n.*?\n---\n", raw, re.S)
    body = raw[len(m.group(0)):] if m else raw
    imgdir = workdir / "images"
    imgdir.mkdir(parents=True, exist_ok=True)
    srcs = sorted(set(re.findall(r'src="([^"]+\.(?:jpg|jpeg|png|webp))"', body)))
    for name in srcs:
        shutil.copyfile(md_path.parent / name, imgdir / name)

    def figure(mm):
        src, alt = mm.group(1), mm.group(2)
        im = Image.open(imgdir / src)
        w, h = im.size
        if max(w, h) > 1400:  # keep the docx small
            sc = 1400 / max(w, h)
            im = im.resize((round(w * sc), round(h * sc)), Image.LANCZOS)
            im.save(imgdir / src, "JPEG", quality=88)
            w, h = im.size
        if h / w > 1.55:  # too tall for a 14.7cm-wide slot: pad with the corner colour
            tw = int(h * 0.86)
            canvas = Image.new("RGB", (tw, h), im.convert("RGB").getpixel((3, 3)))
            canvas.paste(im.convert("RGB"), ((tw - w) // 2, 0))
            canvas.save(imgdir / src, "JPEG", quality=92)
            w, h = canvas.size
        scale = min(520 / w, 600 / h, 1.0)
        return f'<img src="images/{src}" alt="{alt}" width="{round(w*scale)}" height="{round(h*scale)}">'

    body = re.sub(r'<img src="([^"]+)" alt="([^"]*)"[^>]*>', figure, body)
    body = re.sub(r'<figure>(.*?)<figcaption>(.*?)</figcaption>\s*</figure>',
                  lambda mm: mm.group(1) + '\n\n<p class="cap">' + mm.group(2) + '</p>', body, flags=re.S)
    html_body = markdown.markdown(body, extensions=["tables", "md_in_html", "sane_lists"])
    html = (f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<title>{title}</title><style>{CSS}</style></head><body>{html_body}</body></html>')
    out = workdir / "doc.html"
    out.write_text(html, encoding="utf-8")

    imgdir2 = workdir / "images"
    data = re.sub(r'src="images/([^"]+)"',
                  lambda mm: 'src="data:image/jpeg;base64,%s"' % base64.b64encode((imgdir2 / mm.group(1)).read_bytes()).decode(),
                  html)
    out_data = workdir / "doc-datauri.html"
    out_data.write_text(data, encoding="utf-8")
    return out_data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", required=True)
    ap.add_argument("--out-name", required=True, help='e.g. "2 昆山租轮椅体验"')
    ap.add_argument("--image-map", required=True, help="src=中文名;src=中文名")
    args = ap.parse_args()

    draft = Path(args.draft).resolve()
    md = next(p for p in draft.glob("*.md") if p.stem not in {"review"})
    out_dir = Path("out") / args.out_name
    (out_dir / "图片").mkdir(parents=True, exist_ok=True)

    mapping = dict(kv.split("=", 1) for kv in args.image_map.split(";") if kv)
    for src, cn in mapping.items():
        shutil.copyfile(draft / src, out_dir / "图片" / cn)

    work = Path(tempfile.mkdtemp(prefix="pkg-"))
    doc_html = build_doc_html(md, work, args.out_name.split(" ", 1)[-1])
    docx = out_dir / f"{args.out_name.split(' ', 1)[-1]}.docx"
    r = subprocess.run([sys.executable, str(CONVERT), "--html", str(doc_html), "--output", str(docx),
                        "--no-preflight", "--no-verify"], capture_output=True, text=True)
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:])

    html = (draft / f"{md.stem}.html").read_text(encoding="utf-8")
    for src, cn in mapping.items():
        html = html.replace(f'src="{src}"', f'src="图片/{cn}"')
    (out_dir / f"{args.out_name.split(' ', 1)[-1]}.html").write_text(html, encoding="utf-8")
    print("package:", out_dir, sorted(p.name for p in out_dir.iterdir()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
