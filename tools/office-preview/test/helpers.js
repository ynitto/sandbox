'use strict';

// テスト用に、最小の docx / xlsx / pptx をその場で組み立てる（バイナリをリポジトリに置かない）。

const zlib = require('zlib');

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(buf) {
  let c = 0xffffffff;
  for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

// entries: { 'path/in/zip': string | Buffer }。deflate: true で圧縮する
function makeZip(entries, { deflate = true } = {}) {
  const locals = [];
  const centrals = [];
  let offset = 0;
  for (const [name, content] of Object.entries(entries)) {
    const data = Buffer.isBuffer(content) ? content : Buffer.from(content, 'utf8');
    const body = deflate ? zlib.deflateRawSync(data) : data;
    const nameBuf = Buffer.from(name, 'utf8');
    const crc = crc32(data);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x800, 6);
    local.writeUInt16LE(deflate ? 8 : 0, 8);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(body.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBuf.length, 26);
    locals.push(local, nameBuf, body);
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0x800, 8);
    central.writeUInt16LE(deflate ? 8 : 0, 10);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(body.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(nameBuf.length, 28);
    central.writeUInt32LE(offset, 42);
    centrals.push(central, nameBuf);
    offset += 30 + nameBuf.length + body.length;
  }
  const cd = Buffer.concat(centrals);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(Object.keys(entries).length, 8);
  end.writeUInt16LE(Object.keys(entries).length, 10);
  end.writeUInt32LE(cd.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, cd, end]);
}

// 1×1 の PNG
const PNG_1PX = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');

const NS = {
  w: 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
  r: 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
  a: 'http://schemas.openxmlformats.org/drawingml/2006/main',
  p: 'http://schemas.openxmlformats.org/presentationml/2006/main',
  s: 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
  pic: 'http://schemas.openxmlformats.org/drawingml/2006/picture',
  wp: 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
};
const REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';

function rels(list) {
  return '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    + list.map(([id, type, target]) => `<Relationship Id="${id}" Type="${REL}/${type}" Target="${target}"/>`).join('')
    + '</Relationships>';
}

const CONTENT_TYPES = '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>';

function docx(bodyXml, { styles = '', numbering = '', header = '', extraRels = [], media = {} } = {}) {
  const docRels = [['rIdS', 'styles', 'styles.xml'], ...extraRels];
  if (numbering) docRels.push(['rIdN', 'numbering', 'numbering.xml']);
  if (header) docRels.push(['rIdH', 'header', 'header1.xml']);
  const files = {
    '[Content_Types].xml': CONTENT_TYPES,
    '_rels/.rels': rels([['rId1', 'officeDocument', 'word/document.xml']]),
    'word/_rels/document.xml.rels': rels(docRels),
    'word/document.xml': `<?xml version="1.0"?><w:document xmlns:w="${NS.w}" xmlns:r="${NS.r}" xmlns:a="${NS.a}" xmlns:pic="${NS.pic}" xmlns:wp="${NS.wp}"><w:body>${bodyXml}</w:body></w:document>`,
    'word/styles.xml': `<?xml version="1.0"?><w:styles xmlns:w="${NS.w}"><w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="21"/></w:rPr></w:rPrDefault></w:docDefaults>${styles}</w:styles>`,
  };
  if (numbering) files['word/numbering.xml'] = `<?xml version="1.0"?><w:numbering xmlns:w="${NS.w}">${numbering}</w:numbering>`;
  if (header) files['word/header1.xml'] = `<?xml version="1.0"?><w:hdr xmlns:w="${NS.w}">${header}</w:hdr>`;
  for (const [k, v] of Object.entries(media)) files[`word/${k}`] = v;
  return makeZip(files);
}

function xlsx({ sheets = [['Sheet1', '<sheetData/>']], strings = [], styles = '', workbookExtra = '' } = {}) {
  const files = {
    '[Content_Types].xml': CONTENT_TYPES,
    '_rels/.rels': rels([['rId1', 'officeDocument', 'xl/workbook.xml']]),
    'xl/_rels/workbook.xml.rels': rels([
      ...sheets.map((_, i) => [`rId${i + 1}`, 'worksheet', `worksheets/sheet${i + 1}.xml`]),
      ['rIdSS', 'sharedStrings', 'sharedStrings.xml'],
      ['rIdST', 'styles', 'styles.xml'],
    ]),
    'xl/workbook.xml': `<?xml version="1.0"?><workbook xmlns="${NS.s}" xmlns:r="${NS.r}">${workbookExtra}<sheets>${sheets.map(([name, , state], i) => `<sheet name="${name}" sheetId="${i + 1}"${state ? ` state="${state}"` : ''} r:id="rId${i + 1}"/>`).join('')}</sheets></workbook>`,
    'xl/sharedStrings.xml': `<?xml version="1.0"?><sst xmlns="${NS.s}">${strings.map((s) => `<si><t>${s}</t></si>`).join('')}</sst>`,
    'xl/styles.xml': `<?xml version="1.0"?><styleSheet xmlns="${NS.s}">${styles || '<fonts><font><sz val="11"/><name val="Calibri"/></font></fonts><fills><fill><patternFill patternType="none"/></fill></fills><borders><border/></borders><cellXfs><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellXfs>'}</styleSheet>`,
  };
  sheets.forEach(([, xml], i) => { files[`xl/worksheets/sheet${i + 1}.xml`] = `<?xml version="1.0"?><worksheet xmlns="${NS.s}" xmlns:r="${NS.r}">${xml}</worksheet>`; });
  return makeZip(files);
}

// レイアウトに位置を持つタイトルのプレースホルダーと、スライド側の文字だけのタイトル
function pptx(slidesXml, { layoutTree = '', masterTree = '', media = {}, slideRels = [] } = {}) {
  const ph = (type, x, y, cx, cy) => `<p:sp><p:nvSpPr><p:cNvPr id="2" name="t"/><p:cNvSpPr/><p:nvPr><p:ph type="${type}"/></p:nvPr></p:nvSpPr><p:spPr><a:xfrm><a:off x="${x}" y="${y}"/><a:ext cx="${cx}" cy="${cy}"/></a:xfrm></p:spPr><p:txBody><a:bodyPr/><a:p><a:r><a:t>枠の見本の文字</a:t></a:r></a:p></p:txBody></p:sp>`;
  const files = {
    '[Content_Types].xml': CONTENT_TYPES,
    '_rels/.rels': rels([['rId1', 'officeDocument', 'ppt/presentation.xml']]),
    'ppt/_rels/presentation.xml.rels': rels(slidesXml.map((_, i) => [`rId${i + 10}`, 'slide', `slides/slide${i + 1}.xml`])),
    'ppt/presentation.xml': `<?xml version="1.0"?><p:presentation xmlns:p="${NS.p}" xmlns:r="${NS.r}" xmlns:a="${NS.a}"><p:sldIdLst>${slidesXml.map((_, i) => `<p:sldId id="${256 + i}" r:id="rId${i + 10}"/>`).join('')}</p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/></p:presentation>`,
    'ppt/slideLayouts/slideLayout1.xml': `<?xml version="1.0"?><p:sldLayout xmlns:p="${NS.p}" xmlns:a="${NS.a}" xmlns:r="${NS.r}"><p:cSld><p:spTree>${layoutTree || ph('title', 914400, 457200, 10363200, 1143000)}</p:spTree></p:cSld></p:sldLayout>`,
    'ppt/slideLayouts/_rels/slideLayout1.xml.rels': rels([['rId1', 'slideMaster', '../slideMasters/slideMaster1.xml']]),
    'ppt/slideMasters/slideMaster1.xml': `<?xml version="1.0"?><p:sldMaster xmlns:p="${NS.p}" xmlns:a="${NS.a}" xmlns:r="${NS.r}"><p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="102030"/></a:solidFill></p:bgPr></p:bg><p:spTree>${masterTree}</p:spTree></p:cSld><p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/><p:txStyles><p:titleStyle><a:lvl1pPr><a:defRPr sz="4400"/></a:lvl1pPr></p:titleStyle><p:bodyStyle><a:lvl1pPr><a:defRPr sz="2800"/></a:lvl1pPr></p:bodyStyle></p:txStyles></p:sldMaster>`,
    'ppt/slideMasters/_rels/slideMaster1.xml.rels': rels([['rId1', 'theme', '../theme/theme1.xml']]),
    'ppt/theme/theme1.xml': `<?xml version="1.0"?><a:theme xmlns:a="${NS.a}"><a:themeElements><a:clrScheme name="t"><a:dk1><a:srgbClr val="000000"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:accent1><a:srgbClr val="FF8800"/></a:accent1></a:clrScheme><a:fontScheme name="f"><a:majorFont><a:latin typeface="Major Font"/><a:ea typeface=""/></a:majorFont><a:minorFont><a:latin typeface="Minor Font"/><a:ea typeface=""/></a:minorFont></a:fontScheme></a:themeElements></a:theme>`,
  };
  slidesXml.forEach((xml, i) => {
    files[`ppt/slides/slide${i + 1}.xml`] = `<?xml version="1.0"?><p:sld xmlns:p="${NS.p}" xmlns:a="${NS.a}" xmlns:r="${NS.r}"><p:cSld><p:spTree>${xml}</p:spTree></p:cSld></p:sld>`;
    files[`ppt/slides/_rels/slide${i + 1}.xml.rels`] = rels([['rId1', 'slideLayout', '../slideLayouts/slideLayout1.xml'], ...slideRels]);
  });
  for (const [k, v] of Object.entries(media)) files[`ppt/${k}`] = v;
  return makeZip(files);
}

// ページごとに 1 色で塗りつぶした PDF。pages: [{ width, height, rgb: [r, g, b] (0〜1) }]
function pdf(pages) {
  const objects = [];
  const kids = [];
  pages.forEach((p, i) => {
    const pageNo = 3 + i * 2;
    kids.push(`${pageNo} 0 R`);
    const content = `${p.rgb.join(' ')} rg 0 0 ${p.width} ${p.height} re f\n`;
    objects[pageNo] = `<</Type/Page/Parent 2 0 R/MediaBox[0 0 ${p.width} ${p.height}]/Contents ${pageNo + 1} 0 R>>`;
    objects[pageNo + 1] = `<</Length ${content.length}>>\nstream\n${content}endstream`;
  });
  objects[1] = '<</Type/Catalog/Pages 2 0 R>>';
  objects[2] = `<</Type/Pages/Kids[${kids.join(' ')}]/Count ${pages.length}>>`;
  let out = '%PDF-1.4\n';
  const offsets = [];
  for (let n = 1; n < objects.length; n++) {
    offsets[n] = out.length;
    out += `${n} 0 obj\n${objects[n]}\nendobj\n`;
  }
  const xref = out.length;
  out += `xref\n0 ${objects.length}\n0000000000 65535 f \n`;
  for (let n = 1; n < objects.length; n++) out += `${String(offsets[n]).padStart(10, '0')} 00000 n \n`;
  out += `trailer\n<</Size ${objects.length}/Root 1 0 R>>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, 'latin1');
}

module.exports = { makeZip, docx, xlsx, pptx, pdf, PNG_1PX, NS };
