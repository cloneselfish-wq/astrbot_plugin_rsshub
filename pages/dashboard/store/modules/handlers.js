import {
  getHandlers
} from '../../js/api.js';
import {
  prettyJson,
  normalizeHandlers,
  normalizeHandlerRegistryItem
} from '../helpers.js';

export const handlersModule = {
  async loadHandlers() {
    this.handlersLoading = true;
    try {
      const result = await getHandlers();
      this.handlersRegistry = (result.items || []).map(normalizeHandlerRegistryItem);
      if (
        this.expandedHandlerName &&
        !this.handlersRegistry.some((item) => item.name === this.expandedHandlerName)
      ) {
        this.expandedHandlerName = '';
      }
    } catch (err) {
      this.showToast(`加载处理器失败: ${err.message}`, 'error');
    } finally {
      this.handlersLoading = false;
    }
  },

  async refreshHandlers() {
    await this.runPending('handlers:refresh', () => this.loadHandlers()).catch((err) => {
      this.showToast(`刷新处理器失败: ${err.message}`, 'error');
    });
  },

  handlerTypeLabel(type) {
    return String(type || '').trim() === 'builtin' ? '内置' : '扩展';
  },

  handlerStatusLabel(handler) {
    return handler?.default_enabled ? '默认启用' : '默认关闭';
  },

  handlerFieldCountText(handler) {
    const count = Array.isArray(handler?.fields) ? handler.fields.length : 0;
    return count > 0 ? `${count} 个配置项` : '无额外配置';
  },

  isHandlerExpanded(name) {
    return String(this.expandedHandlerName || '') === String(name || '');
  },

  toggleHandlerCard(name) {
    const normalized = String(name || '');
    this.expandedHandlerName = this.isHandlerExpanded(normalized) ? '' : normalized;
  },

  handlerDefaultValueText(value) {
    if (value === undefined) return '-';
    if (value === null) return 'null';
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    if (typeof value === 'string') return value || '""';
    if (typeof value === 'number') return String(value);
    return prettyJson(value);
  },

  applyHandlersJson(form) {
    try {
      const raw = JSON.parse(form.handlers_json || '[]');
      if (!Array.isArray(raw)) throw new Error('必须是 JSON 数组');
      const normalized = normalizeHandlers(raw);
      form.handlers_json = JSON.stringify(normalized, null, 2);
      const dropped = raw.length - normalized.length;
      if (dropped > 0) {
        this.showToast(
          `处理链 JSON 已应用，但 ${dropped} 个条目因缺少 name 被丢弃`,
          'error'
        );
      } else {
        this.showToast('处理链 JSON 已应用');
      }
    } catch (err) {
      this.showToast(`处理链 JSON 格式错误: ${err.message}`, 'error');
    }
  }
};
