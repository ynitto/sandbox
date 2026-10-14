import { describe, expect, it } from 'vitest';
import { sanitizeSvg } from './sanitize';

const png = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lZkAAAAASUVORK5CYII=';
describe('SVG import', () => {
  it('namespaces references and retains supported transforms and clips', () => {
    const input = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><defs><clipPath id="clip"><rect width="10" height="10"/></clipPath></defs><g transform="translate(2 3)" clip-path="url(#clip)"><image id="photo" href="${png}" width="10" height="10"/></g></svg>`;
    const a = sanitizeSvg(input, 'one'); const b = sanitizeSvg(input, 'two');
    expect(a.editableIds).toEqual(['photo']);
    expect(a.markup).toContain('url(#one-clip)'); expect(a.markup).toContain('transform="translate(2 3)"');
    expect(b.markup).toContain('id="two-photo"');
  });
  it('removes scripts, handlers and unsafe URLs with a notice', () => {
    const input = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" onload="alert(1)"><script>alert(1)</script><image id="a" href="javascript:alert(1)"/></svg>`;
    const output = sanitizeSvg(input, 'safe');
    expect(output.markup).not.toContain('script'); expect(output.markup).not.toContain('javascript:');
    expect(output.editableIds).toEqual([]); expect(output.notices[0].code).toBe('removed-content');
  });
  it('accepts extra tags, attributes and editable rules but never script or handlers', () => {
    const input = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><title>t</title><g id="badge" letter-spacing="2" onclick="x()"><rect width="2" height="2"/></g><script>x()</script><image id="photo" href="${png}"/></svg>`;
    const output = sanitizeSvg(input, 'p', { tags: ['title', 'script'], attributes: ['letter-spacing', 'onclick'], isEditable: (el, id) => id === 'badge' });
    expect(output.editableIds).toEqual(['badge']);
    expect(output.markup).toContain('<title>t</title>'); expect(output.markup).toContain('letter-spacing="2"');
    expect(output.markup).not.toContain('script'); expect(output.markup).not.toContain('onclick');
  });
});
