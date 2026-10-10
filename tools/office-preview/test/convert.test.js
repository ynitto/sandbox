'use strict';

// docx / xlsx / pptx → HTML（Electron なしで確かめられる部分）

const { test } = require('node:test');
const assert = require('node:assert');
const { toHtml, supports } = require('../src');
const { formatValue, colName } = require('../src/xlsx');
const { docx, xlsx, pptx, makeZip, PNG_1PX } = require('./helpers');

const visibleText = (html) => html.replace(/<style[\s\S]*?<\/style>/g, '').replace(/<[^>]+>/g, '').replace(/​/g, '');

// ---- 共通 ----

test('種類は拡張子でなく中身で決め、対応していない ZIP は UNSUPPORTED', async () => {
  const r = await toHtml(docx('<w:p><w:r><w:t>本文</w:t></w:r></w:p>'));
  assert.strictEqual(r.type, 'docx');
  await assert.rejects(toHtml(makeZip({ 'hello.txt': 'x' })), { code: 'UNSUPPORTED' });
  assert.ok(supports('a.PPTX') && supports('b.xlsm') && supports('c.docx'));
  assert.ok(!supports('d.doc') && !supports('e.pdf'));
});

test('出力の HTML は CSP でスクリプトと外部の読み込みを止め、本文は文字として逃がす', async () => {
  const r = await toHtml(docx('<w:p><w:r><w:t>&lt;script&gt;alert(1)&lt;/script&gt;&lt;img src=x onerror=alert(1)&gt;</w:t></w:r></w:p>'));
  assert.match(r.html, /Content-Security-Policy" content="default-src 'none'; img-src data:/);
  assert.ok(!/<script>/i.test(r.body));
  assert.ok(!/<img src=x/i.test(r.body));
  assert.ok(r.body.includes('&lt;script&gt;'));
});

// ---- docx ----

test('docx: 書式・段落番号・表を描き、改ページの先は描かない', async () => {
  const numbering = '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:pPr><w:ind w:left="420" w:hanging="420"/></w:pPr></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>';
  const item = (t) => `<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>${t}</w:t></w:r></w:p>`;
  const body = [
    '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:rPr><w:b/><w:color w:val="C00000"/><w:sz w:val="32"/></w:rPr><w:t>表題</w:t></w:r></w:p>',
    item('一つ目'), item('二つ目'),
    '<w:tbl><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4"/><w:insideH w:val="single" w:sz="4"/></w:tblBorders></w:tblPr><w:tblGrid><w:gridCol w:w="2000"/><w:gridCol w:w="2000"/></w:tblGrid>'
      + '<w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>結合</w:t></w:r></w:p></w:tc></w:tr>'
      + '<w:tr><w:tc><w:p><w:r><w:t>左</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>右</w:t></w:r></w:p></w:tc></w:tr></w:tbl>',
    '<w:p><w:r><w:t>改ページの前</w:t></w:r><w:r><w:br w:type="page"/></w:r><w:r><w:t>次のページ</w:t></w:r></w:p>',
    '<w:p><w:r><w:t>2 ページ目の段落</w:t></w:r></w:p>',
    '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720"/><w:headerReference w:type="default" r:id="rIdH"/></w:sectPr>',
  ].join('');
  const r = await toHtml(docx(body, { numbering, header: '<w:p><w:r><w:t>社外秘</w:t></w:r></w:p>' }));
  const text = visibleText(r.body);
  assert.strictEqual(Math.round(r.width), 794); // A4
  assert.strictEqual(Math.round(r.height), 1123);
  assert.match(r.body, /font-weight:bold[^"]*color:#C00000|color:#C00000[^"]*font-weight:bold/);
  assert.match(r.body, /font-size:16pt/);
  assert.match(text, /1\.一つ目/);
  assert.match(text, /2\.二つ目/);
  assert.match(r.body, /colspan="2"/);
  assert.ok(text.includes('社外秘'));
  assert.ok(text.includes('改ページの前'));
  assert.ok(!text.includes('次のページ'));
  assert.ok(!text.includes('2 ページ目の段落'));
});

test('docx: スタイルの継承（basedOn）と、フィールドは結果だけを描く', async () => {
  const styles = '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:rPr><w:sz w:val="20"/></w:rPr></w:style>'
    + '<w:style w:type="paragraph" w:styleId="H1"><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style>'
    + '<w:style w:type="paragraph" w:styleId="H1Red"><w:basedOn w:val="H1"/><w:rPr><w:color w:val="FF0000"/></w:rPr></w:style>';
  const body = '<w:p><w:pPr><w:pStyle w:val="H1Red"/></w:pPr><w:r><w:t>見出し</w:t></w:r></w:p>'
    + '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> PAGE </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>7</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>';
  const r = await toHtml(docx(body, { styles }));
  assert.match(r.body, /font-size:14pt;font-weight:bold;color:#FF0000">見出し/);
  const text = visibleText(r.body);
  assert.ok(text.includes('7'));
  assert.ok(!text.includes('PAGE'));
});

test('docx: 埋め込み画像は data: URI で、行グリッドがあれば行送りの倍数で並べる', async () => {
  const drawing = '<w:drawing><wp:inline><wp:extent cx="952500" cy="476250"/><a:graphic><a:graphicData><pic:pic><pic:blipFill><a:blip r:embed="rIdImg"/></pic:blipFill></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing>';
  const body = `<w:p><w:r>${drawing}</w:r></w:p><w:p><w:r><w:t>本文</w:t></w:r></w:p><w:sectPr><w:docGrid w:type="lines" w:linePitch="360"/></w:sectPr>`;
  const r = await toHtml(docx(body, { extraRels: [['rIdImg', 'image', 'media/image1.png']], media: { 'media/image1.png': PNG_1PX } }));
  assert.match(r.body, /<img src="data:image\/png;base64,[^"]+" style="width:100.0px;height:50.0px/);
  assert.match(r.body, /line-height:24\.0px/); // 18pt の行送り
});

// ---- xlsx ----

test('xlsx: 表示形式（桁区切り・%・日付・負の数の色・時刻）', () => {
  assert.strictEqual(formatValue(1234567.891, '#,##0').text, '1,234,568');
  assert.strictEqual(formatValue(1234.5, '#,##0.00').text, '1,234.50');
  assert.strictEqual(formatValue(0.1234, '0.0%').text, '12.3%');
  assert.strictEqual(formatValue(46031, 'yyyy/m/d').text, '2026/1/9');
  assert.strictEqual(formatValue(46031, 'yyyy"年"m"月"d"日"(aaa)').text, '2026年1月9日(金)');
  assert.strictEqual(formatValue(0.75, 'h:mm').text, '18:00');
  assert.strictEqual(formatValue(1.5, '[h]:mm').text, '36:00');
  assert.strictEqual(formatValue(0.5, 'h:mm AM/PM').text, '12:00 PM');
  const neg = formatValue(-1500, '"¥"#,##0;[Red]"¥"\\-#,##0');
  assert.deepStrictEqual(neg, { text: '¥-1,500', color: '#FF0000' });
  assert.strictEqual(formatValue(-3, '0.0').text, '-3.0');
  assert.strictEqual(formatValue(0, '0;-0;"ゼロ"').text, 'ゼロ');
  assert.strictEqual(formatValue(12345678901234, 'General').text, '1.23457E+13');
  assert.strictEqual(formatValue(0.1 + 0.2, 'General').text, '0.3');
  assert.strictEqual(formatValue(60, 'yyyy/m/d').text, '1900/2/29'); // Excel の 1900 年うるう年の誤りに合わせる
  assert.strictEqual(formatValue(61, 'yyyy/m/d').text, '1900/3/1');
  assert.strictEqual(colName(1), 'A');
  assert.strictEqual(colName(28), 'AB');
  assert.strictEqual(colName(703), 'AA' + 'A');
});

test('xlsx: 共有文字列・結合・書式付きの数値を描き、隠しシートは開かない', async () => {
  const styles = '<numFmts><numFmt numFmtId="164" formatCode="#,##0"/></numFmts>'
    + '<fonts><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="14"/><color rgb="FF1F4E79"/><name val="Calibri"/></font></fonts>'
    + '<fills><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFF2CC"/></patternFill></fill></fills>'
    + '<borders><border/></borders>'
    + '<cellXfs><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0"/></cellXfs>';
  const sheet = '<sheetData><row r="1"><c r="A1" t="s" s="1"><v>0</v></c></row><row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2" s="2"><v>1234567</v></c><c r="C2" t="b"><v>1</v></c></row></sheetData><mergeCells><mergeCell ref="A1:C1"/></mergeCells>';
  const book = xlsx({ sheets: [['隠し', '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>見えてはいけない</t></is></c></row></sheetData>', 'hidden'], ['売上', sheet]], strings: ['月次売上', '合計'], styles });
  const r = await toHtml(book);
  assert.strictEqual(r.sheet, '売上');
  const text = visibleText(r.body);
  assert.ok(text.includes('月次売上') && text.includes('1,234,567') && text.includes('TRUE'));
  assert.ok(!text.includes('見えてはいけない'));
  assert.match(r.body, /colspan="3"/);
  assert.match(r.body, /background:#FFF2CC/);
  assert.match(r.body, /font-weight:bold;color:#1F4E79/);
  const other = await toHtml(book, { sheet: '隠し' });
  assert.ok(visibleText(other.body).includes('見えてはいけない'));
});

test('xlsx: 10 万行のシートも、見える範囲だけを素早く描く', async () => {
  const rows = [];
  for (let r = 1; r <= 100000; r++) rows.push(`<row r="${r}"><c r="A${r}"><v>${r}</v></c><c r="B${r}" t="inlineStr"><is><t>行${r}の説明文</t></is></c></row>`);
  const sheet = `<sheetData>${rows.join('')}</sheetData><mergeCells><mergeCell ref="C1:D2"/></mergeCells>`;
  const started = Date.now();
  const r = await toHtml(xlsx({ sheets: [['大きい', sheet]] }));
  const text = visibleText(r.body);
  assert.ok(text.includes('行1の説明文') && text.includes('行20の説明文'));
  assert.ok(!text.includes('行99999の説明文'));
  assert.match(r.body, /colspan="2" rowspan="2"/); // 切ったあとも、行の後ろの結合は読む
  assert.ok(Date.now() - started < 5000, `${Date.now() - started}ms`);
});

// ---- pptx ----

test('pptx: 位置をレイアウトから引き継ぎ、レイアウトの枠の見本の文字は描かない', async () => {
  const title = '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:p><a:r><a:t>スライドの題</a:t></a:r></a:p></p:txBody></p:sp>';
  const box = '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Box"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="952500" cy="952500"/></a:xfrm><a:prstGeom prst="ellipse"/><a:solidFill><a:schemeClr val="accent1"/></a:solidFill></p:spPr></p:sp>';
  const r = await toHtml(pptx([title + box, '<p:sp><p:nvSpPr><p:cNvPr id="2" name="x"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="100" cy="100"/></a:xfrm></p:spPr><p:txBody><a:bodyPr/><a:p><a:r><a:t>2 枚目</a:t></a:r></a:p></p:txBody></p:sp>']));
  assert.strictEqual(Math.round(r.width), 1280);
  assert.strictEqual(r.height, 720);
  assert.strictEqual(r.slideCount, 2);
  const text = visibleText(r.body);
  assert.ok(text.includes('スライドの題'));
  assert.ok(!text.includes('枠の見本の文字'));
  assert.ok(!text.includes('2 枚目'));
  assert.match(r.body, /left:96px;top:48px;width:1088px;height:120px/); // レイアウトの位置（EMU → px）
  assert.match(r.body, /font-size:44pt/); // マスターの titleStyle
  assert.match(r.body, /'Minor Font'/); // テーマのフォント
  assert.match(r.body, /background:#FF8800;border-radius:50%/); // テーマ色の楕円
  assert.match(r.body, /background:#102030/); // マスターの背景
  const second = await toHtml(pptx(['', '<p:sp><p:nvSpPr><p:cNvPr id="2" name="x"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="100" cy="100"/></a:xfrm></p:spPr><p:txBody><a:bodyPr/><a:p><a:r><a:t>2 枚目</a:t></a:r></a:p></p:txBody></p:sp>']), { slide: 1 });
  assert.ok(visibleText(second.body).includes('2 枚目'));
});

test('pptx: 画像（SVG があればそちら）・グループの縮尺・既定の表のスタイル', async () => {
  const pic = '<p:pic><p:nvPicPr><p:cNvPr id="4" name="p"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="rIdPng"><a:extLst><a:ext uri="{96DAC541-7B7A-43D3-8B79-37D633B846F1}"><asvg:svgBlip xmlns:asvg="http://schemas.microsoft.com/office/drawing/2016/SVG/main" r:embed="rIdSvg"/></a:ext></a:extLst></a:blip></p:blipFill><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="952500" cy="952500"/></a:xfrm></p:spPr></p:pic>';
  const group = '<p:grpSp><p:nvGrpSpPr><p:cNvPr id="5" name="g"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="952500" y="952500"/><a:ext cx="1905000" cy="1905000"/><a:chOff x="0" y="0"/><a:chExt cx="952500" cy="952500"/></a:xfrm></p:grpSpPr>'
    + '<p:sp><p:nvSpPr><p:cNvPr id="6" name="c"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="476250" y="0"/><a:ext cx="476250" cy="476250"/></a:xfrm><a:solidFill><a:srgbClr val="00FF00"/></a:solidFill></p:spPr></p:sp></p:grpSp>';
  const table = '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="7" name="t"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr><p:xfrm><a:off x="0" y="3000000"/><a:ext cx="1905000" cy="762000"/></p:xfrm><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table"><a:tbl><a:tblPr firstRow="1" bandRow="1"><a:tableStyleId>{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}</a:tableStyleId></a:tblPr><a:tblGrid><a:gridCol w="952500"/><a:gridCol w="952500"/></a:tblGrid>'
    + '<a:tr h="381000"><a:tc><a:txBody><a:bodyPr/><a:p><a:r><a:t>見出し</a:t></a:r></a:p></a:txBody><a:tcPr/></a:tc><a:tc><a:txBody><a:bodyPr/><a:p><a:r><a:t>B</a:t></a:r></a:p></a:txBody><a:tcPr/></a:tc></a:tr>'
    + '<a:tr h="381000"><a:tc><a:txBody><a:bodyPr/><a:p><a:r><a:t>1</a:t></a:r></a:p></a:txBody><a:tcPr/></a:tc><a:tc><a:txBody><a:bodyPr/><a:p><a:r><a:t>2</a:t></a:r></a:p></a:txBody><a:tcPr/></a:tc></a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame>';
  const svg = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect width="1" height="1"/></svg>');
  const r = await toHtml(pptx([pic + group + table], {
    slideRels: [['rIdPng', 'image', '../media/image1.png'], ['rIdSvg', 'image', '../media/image2.svg']],
    media: { 'media/image1.png': PNG_1PX, 'media/image2.svg': svg },
  }));
  assert.match(r.body, /<img src="data:image\/svg\+xml;base64,/);
  assert.ok(!r.body.includes('data:image/png'));
  // 子の座標は chOff/chExt → off/ext で 2 倍になる
  assert.match(r.body, /left:200px;top:100px;width:100px;height:100px"><div style="position:absolute;inset:0;box-sizing:border-box;background:#00FF00/);
  // 見出し行はアクセント 1 の塗りと白の太字
  assert.match(r.body, /background:#FF8800[^>]*>(?:(?!<\/td>).)*font-weight:bold(?:(?!<\/td>).)*color:#FFFFFF[^>]*>見出し/);
});

test('pptx: スライドが無いプレゼンテーションでも白紙を返す', async () => {
  const r = await toHtml(pptx([]));
  assert.strictEqual(r.slideCount, undefined);
  assert.match(r.body, /background:#fff/);
});
