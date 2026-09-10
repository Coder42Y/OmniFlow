import { marked } from 'marked';

// 配置 marked 解析规则
const renderer = new marked.Renderer();

// 链接新窗口打开并防钓鱼
renderer.link = ({ href, title, text }) => {
  const titleAttr = title ? ` title="${title}"` : '';
  const cleanHref = href || '#';
  return `<a href="${cleanHref}" target="_blank" rel="noopener noreferrer"${titleAttr}>${text}</a>`;
};

// 表格增加响应式包裹容器
renderer.table = (token) => {
  const header = token.header
    .map((cell) => `<th align="${cell.align || 'left'}">${cell.text}</th>`)
    .join('');
  const rows = token.rows
    .map(
      (row) =>
        `<tr>${row
          .map((cell) => `<td align="${cell.align || 'left'}">${cell.text}</td>`)
          .join('')}</tr>`,
    )
    .join('');
  return `<div class="table-container"><table><thead><tr>${header}</tr></thead><tbody>${rows}</tbody></table></div>`;
};

marked.use({
  renderer,
  breaks: true,
  gfm: true,
});

/**
 * 将 Markdown 文本安全渲染为 HTML
 * @param {string} text - 输入的 Markdown 文本
 * @returns {string} 渲染后的 HTML 字符串
 */
export function renderMarkdown(text) {
  if (!text) return '';
  try {
    return marked.parse(text);
  } catch (err) {
    console.warn('marked parse error:', err);
    // 兜底轻量转义
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\n/g, '<br>');
  }
}
