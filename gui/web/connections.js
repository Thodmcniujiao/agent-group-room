import { catalogText } from './model-directory.js';
import { AccountUsageView } from './account-usage.js';
// Credentials belong to saved agents, independently of current team membership.
export function groupAgents(agents) {
  const groups = new Map();
  for (const agent of agents) {
    const name = agent.group || agent.backend;
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(agent);
  }
  return groups;
}

export function keyHelp(member) {
  if (member.backend === 'deepseek') return 'https://platform.deepseek.com/api_keys';
  try {
    if (new URL(member.base_url).hostname === 'generativelanguage.googleapis.com') {
      return 'https://aistudio.google.com/api-keys';
    }
  } catch { /* A CLI connection has no API URL. */ }
  return '';
}

export class ConnectionsPanel {
  constructor(root, { onSaveKey, onLogin, onEdit, onDebug, onUsageRefresh, onAvatar }) {
    this.root = root;
    this.actions = { onSaveKey, onLogin, onEdit, onDebug, onUsageRefresh, onAvatar };
    this.cards = new Map();
    this.groups = new Map();
  }

  render(members, statuses, catalogs = {}, usage = {}, logins = {}) {
    const grouped = groupAgents(members);
    for (const [name, group] of this.groups) {
      if (!grouped.has(name)) { group.root.remove(); this.groups.delete(name); }
    }
    for (const [name, agents] of grouped) {
      let group = this.groups.get(name);
      if (!group) {
        const root = document.createElement('section'); root.className = 'agent-group';
        const heading = document.createElement('h3'); const body = document.createElement('div');
        root.append(heading, body); group = {root, heading, body}; this.groups.set(name, group);
      }
      group.heading.textContent = name + ' · ' + agents.length;
      const index = [...grouped.keys()].indexOf(name);
      if (this.root.children[index] !== group.root) this.root.insertBefore(group.root, this.root.children[index] || null);
    }
    const current = new Set(members.map(member => member.id));
    for (const [id, card] of this.cards) {
      if (!current.has(id)) { card.root.remove(); this.cards.delete(id); }
    }
    for (const member of members) {
      let card = this.cards.get(member.id);
      if (card && card.backend !== member.backend) {
        card.root.remove(); this.cards.delete(member.id); card = null;
      }
      if (!card) {
        card = this.create(member);
        this.cards.set(member.id, card);
      }
      card.member = member;
      const state = statuses[member.id] || {};
      card.name.textContent = member.name;
      card.avatar.dataset.avatar=member.id;card.avatar.dataset.letter=member.name==='ChatGPT'?'G':member.name.slice(0,1).toUpperCase();card.avatar.textContent=card.avatar.dataset.letter;card.avatar.setAttribute('aria-label','设置 '+member.name+' 的头像');card.avatar.onclick=()=>this.actions.onAvatar(member.id);
      card.model.textContent = '默认模型 · ' + member.model;
      card.usage.render(usage[member.id]);
      card.catalog.textContent = catalogText(catalogs[member.id]);
      const action = catalogs[member.id]?.action_url;
      card.catalogAction.hidden = !action;
      if (action) { card.catalogAction.href = action; card.catalogAction.textContent = (catalogs[member.id].action_label || '查看设置') + ' ↗'; }
      card.status.textContent = state.detail || '等待同步';
      card.edit.textContent = '编辑 ' + member.name;
      card.debug.setAttribute('aria-label', '调试 ' + member.name);
      card.status.classList.toggle('ready', Boolean(state.ready));
      if (['chatgpt', 'claude'].includes(member.backend)) {
        const login = logins[member.backend];
        card.waiting = login?.status === 'waiting';
        card.toggle.disabled = Boolean(card.waiting || card.starting);
        if (login) card.note.textContent = login.message;
      }
      card.edit.textContent = '编辑 ' + member.name;
      card.debug.setAttribute('aria-label', '调试 ' + member.name);
      if (card.input) {
        if (card.saved) card.note.textContent = member.name + '的密钥已加密保存。';
        if (card.endpoint !== member.base_url) {
          card.saved = false;
          if (card.input.value) card.note.textContent = '连接地址已更改，请确认后重新输入此成员的密钥。';
          card.input.value = '';
          card.endpoint = member.base_url;
        }
        card.endpointLabel.textContent = member.base_url;
        card.label.textContent = member.name + ' API 密钥';
        card.input.setAttribute('aria-label', member.name + ' API 密钥');
        card.input.placeholder = '粘贴 ' + member.name + ' 的密钥；已有密钥不会回显';
        card.save.textContent = '保存 ' + member.name + ' 密钥';
        card.toggle.textContent = (state.ready ? '更新 ' : '配置 ') + member.name + ' 密钥';
        card.toggle.hidden = member.no_auth;
        if (member.no_auth) card.form.hidden = true;
        const help = keyHelp(member);
        card.help.hidden = !help;
        if (help) {
          card.help.href = help;
          card.help.textContent = '获取 ' + member.name + ' API 密钥';
        }
      }
      // append only when necessary so polling does not steal keyboard focus.
      const group = this.groups.get(member.group || member.backend);
      const position = grouped.get(member.group || member.backend).indexOf(member);
      const atPosition = group.body.children[position];
      if (atPosition !== card.root) group.body.insertBefore(card.root, atPosition || null);
    }
  }

  create(member) {
    const root = document.createElement('section');
    root.className = 'connection-card';
    root.dataset.member = member.id;
    const cli = ['chatgpt', 'claude'].includes(member.backend);
    root.innerHTML = `
      <div class="connection-card-top">
        <button type="button" class="avatar small avatar-edit"></button>
        <div class="connection-identity"><strong></strong><span class="connection-model"></span></div>
        <span class="connection-status" role="status"></span>
      </div>
      <p class="connection-endpoint"></p>
      <section class="connection-usage" aria-label="账户用量与余额"></section>
      <p class="connection-catalog"></p>
      <a class="catalog-action" target="_blank" rel="noreferrer" hidden>启用 Gemini API ↗</a>
      <div class="connection-actions"><button type="button" class="outline credential-toggle"></button><button type="button" class="connection-edit"></button><button type="button" class="outline agent-debug">调试控制台</button></div>
      <form class="credential-form" hidden>
        <label></label>
        <input type="password" autocomplete="off" spellcheck="false" required>
        <div class="credential-actions"><a target="_blank" rel="noreferrer"></a><button type="submit" class="primary"></button></div>
      </form>
      <p class="credential-note" role="status"></p>`;
    const card = {
      root, member, backend: member.backend, endpoint: member.base_url,
      avatar: root.querySelector('.avatar'), name: root.querySelector('strong'),
      model: root.querySelector('.connection-model'), status: root.querySelector('.connection-status'),
      catalog: root.querySelector('.connection-catalog'),
      catalogAction: root.querySelector('.catalog-action'),
      endpointLabel: root.querySelector('.connection-endpoint'),
      toggle: root.querySelector('.credential-toggle'), edit: root.querySelector('.connection-edit'),
      debug: root.querySelector('.agent-debug'),
      form: root.querySelector('form'), label: root.querySelector('label'),
      input: root.querySelector('input'), save: root.querySelector('[type=submit]'),
      help: root.querySelector('.credential-actions a'), note: root.querySelector('.credential-note'),
    };
    card.avatar.classList.add(member.backend === 'openai-compatible' ? 'custom' : member.backend);
    card.usage=new AccountUsageView(root.querySelector('.connection-usage'),()=>this.actions.onUsageRefresh(card.member.id));
    card.edit.onclick = () => this.actions.onEdit(card.member.id);
    card.debug.onclick = () => this.actions.onDebug(card.member.id);
    if (cli) {
      card.form.remove(); card.input = null;
      card.endpointLabel.textContent = member.backend === 'chatgpt' ? 'ChatGPT 本机账户登录' : 'Claude 本机账户登录';
      card.toggle.textContent = member.backend === 'chatgpt' ? '登录 ChatGPT' : '登录 Claude';
      card.toggle.onclick = async () => {
        card.starting = true;
        card.toggle.disabled = true;
        card.note.textContent = '正在启动登录程序…';
        try { card.note.textContent = await this.actions.onLogin(card.member.backend); }
        catch (error) { card.note.textContent = error.message; }
        finally { card.starting = false; card.toggle.disabled = Boolean(card.waiting); }
      };
    } else {
      const inputId = 'connection-key-' + member.id;
      card.input.id = inputId; card.label.htmlFor = inputId;
      card.toggle.onclick = () => {
        card.form.hidden = !card.form.hidden;
        if (!card.form.hidden) card.input.focus();
      };
      card.form.onsubmit = async event => {
        event.preventDefault();
        const value = card.input.value.trim();
        if (!value) return;
        card.save.disabled = true;
        try {
          await this.actions.onSaveKey(card.member.id, value);
          card.input.value = '';
          card.saved = true;
          card.note.textContent = card.member.name + '的密钥已加密保存。';
        } catch (error) { card.note.textContent = error.message; }
        finally { card.save.disabled = false; }
      };
    }
    return card;
  }
}
