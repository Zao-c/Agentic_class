const form = document.querySelector('#chat-form');
const input = document.querySelector('#message');
const conversation = document.querySelector('#conversation');
const dashboard = document.querySelector('#dashboard');
const tracePanel = document.querySelector('#trace-panel');
const traceSteps = document.querySelector('#trace-steps');
const traceStatus = document.querySelector('#trace-status');
const workspaceTitle = document.querySelector('#workspace-title');
const serviceStatus = document.querySelector('#service-status');
const appStatus = document.querySelector('#app-status');
const userId = localStorage.getItem('robot-tutor-user') || `student-${crypto.randomUUID()}`;
const sessionId = sessionStorage.getItem('robot-tutor-session') || `session-${crypto.randomUUID()}`;
localStorage.setItem('robot-tutor-user', userId);
sessionStorage.setItem('robot-tutor-session', sessionId);

const masteryLabels = {
  not_started: '尚未开始',
  needs_review: '需要复习',
  developing: '正在掌握',
  proficient: '已经掌握'
};

function announce(message) {
  appStatus.textContent = '';
  window.setTimeout(() => { appStatus.textContent = message; }, 20);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      message = body.detail?.message || body.message || message;
    } catch (_) { /* keep status message */ }
    throw new Error(message);
  }
  return response.json();
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function addMessage(kind, text) {
  const article = element('article', `message ${kind}`);
  const avatar = element('div', 'avatar', kind === 'user' ? '我' : '助');
  avatar.setAttribute('aria-hidden', 'true');
  const bubble = element('div', 'bubble', text);
  article.append(avatar, bubble);
  conversation.append(article);
  conversation.scrollTop = conversation.scrollHeight;
  return bubble;
}

function traceLabel(type) {
  return ({
    'intent.classified': '识别任务',
    'query.normalized': '整理查询',
    'context.checked': '检查信息',
    'tools.planned': '规划工具',
    'tool.finished': '工具完成',
    'evidence.judged': '判断证据',
    'safety.checked': '安全检查',
    'clarification.requested': '等待补充',
    'answer.completed': '生成回答',
    'answer.abstained': '保守拒答',
    'teacher.escalated': '人工转交'
  })[type];
}

function showChat(prompt = '') {
  conversation.hidden = false;
  dashboard.hidden = true;
  form.hidden = false;
  workspaceTitle.textContent = '今天想解决什么问题？';
  if (prompt) input.value = prompt;
  input.focus();
}

function setActiveNavigation(button) {
  document.querySelectorAll('.nav-item').forEach(item => {
    const active = item === button;
    item.classList.toggle('active', active);
    if (active) item.setAttribute('aria-current', 'page');
    else item.removeAttribute('aria-current');
  });
}

function showDashboard(title) {
  conversation.hidden = true;
  dashboard.hidden = false;
  form.hidden = true;
  tracePanel.hidden = true;
  workspaceTitle.textContent = title;
  dashboard.replaceChildren(element('div', 'loading-card', '正在读取最新数据…'));
}

function metricCard(label, value, note) {
  const card = element('article', 'metric-card');
  card.append(element('span', 'metric-label', label), element('strong', '', String(value)));
  if (note) card.append(element('small', '', note));
  return card;
}

function statusPill(status) {
  return element('span', `mastery-status status-${status}`, masteryLabels[status] || status);
}

function renderError(container, error) {
  const alert = element('div', 'inline-alert', error.message);
  alert.setAttribute('role', 'alert');
  container.replaceChildren(alert);
  announce(error.message);
}

async function renderStudentProgress() {
  showDashboard('我的学习进度');
  try {
    const [progressData, exerciseData] = await Promise.all([
      api(`/api/v1/students/${encodeURIComponent(userId)}/progress`, {
        headers: {'X-User-ID': userId}
      }),
      api(`/api/v1/students/${encodeURIComponent(userId)}/exercises`, {
        headers: {'X-User-ID': userId}
      })
    ]);
    const points = progressData.items;
    const assessed = points.filter(item => item.attempts > 0);
    const average = assessed.length
      ? Math.round(assessed.reduce((sum, item) => sum + item.mastery_score, 0) / assessed.length)
      : 0;
    const openExercises = exerciseData.items.filter(item => item.status === 'open');
    const fragment = document.createDocumentFragment();
    const intro = element('div', 'dashboard-intro');
    intro.append(
      element('p', 'eyebrow', 'PERSONAL LEARNING'),
      element('p', 'dashboard-copy', '掌握度来自已提交练习的可解释评分；没有作答的知识点不会被推测。')
    );
    fragment.append(intro);
    const metrics = element('div', 'metric-grid');
    metrics.append(
      metricCard('已评测知识点', `${assessed.length}/${points.length}`, '只统计真实作答'),
      metricCard('平均掌握度', `${average}%`, assessed.length ? '历次得分累计平均' : '完成练习后更新'),
      metricCard('待完成练习', openExercises.length, '可回到对话继续作答')
    );
    fragment.append(metrics);
    const grid = element('div', 'progress-grid');
    points.forEach(item => {
      const card = element('article', 'progress-card');
      const head = element('div', 'progress-head');
      head.append(element('h3', '', item.name), statusPill(item.mastery_status));
      const score = item.mastery_score ?? 0;
      const progress = element('progress', 'mastery-progress');
      progress.max = 100;
      progress.value = score;
      progress.setAttribute('aria-label', `${item.name}掌握度 ${score}%`);
      card.append(
        head,
        element('p', 'progress-meta', `${item.category} · ${item.attempts} 次作答`),
        progress,
        element('div', 'score-line', item.attempts ? `掌握度 ${score}% · 最近得分 ${item.last_score}%` : '尚无评分记录')
      );
      grid.append(card);
    });
    fragment.append(grid);
    dashboard.replaceChildren(fragment);
    announce(`已加载 ${points.length} 个知识点的学习进度`);
  } catch (error) {
    renderError(dashboard, error);
  }
}

async function renderTeacherSummary() {
  showDashboard('班级知识点概览');
  try {
    const data = await api('/api/v1/classes/progress-summary', {
      headers: {'X-Role': 'teacher'}
    });
    const items = data.items;
    const totalAssessments = items.reduce((sum, item) => sum + item.assessed_students, 0);
    const scored = items.filter(item => item.average_mastery !== null);
    const average = scored.length
      ? Math.round(scored.reduce((sum, item) => sum + item.average_mastery, 0) / scored.length)
      : 0;
    const needsReview = items.reduce((sum, item) => sum + item.needs_review, 0);
    const fragment = document.createDocumentFragment();
    const intro = element('div', 'dashboard-intro');
    intro.append(
      element('p', 'eyebrow', 'TEACHER AGGREGATE'),
      element('p', 'dashboard-copy', '本页仅展示知识点级人数和分数聚合，不返回学生姓名、用户编号或原始答案。')
    );
    fragment.append(intro);
    const metrics = element('div', 'metric-grid');
    metrics.append(
      metricCard('知识点评测人次', totalAssessments, '同一学生不同知识点分别统计'),
      metricCard('知识点平均掌握度', `${average}%`, '不包含尚未评测知识点'),
      metricCard('需要复习', needsReview, '知识点-学生聚合计数')
    );
    fragment.append(metrics);
    const tableWrap = element('div', 'table-wrap');
    const table = element('table', 'summary-table');
    const caption = element('caption', 'sr-only', '班级知识点掌握度匿名聚合');
    const thead = document.createElement('thead');
    const headerRow = document.createElement('tr');
    ['知识点', '评测人数', '平均掌握度', '需复习', '掌握中', '已掌握'].forEach(label => {
      const th = element('th', '', label);
      th.scope = 'col';
      headerRow.append(th);
    });
    thead.append(headerRow);
    const tbody = document.createElement('tbody');
    items.forEach(item => {
      const row = document.createElement('tr');
      [
        item.name,
        item.assessed_students,
        item.average_mastery === null ? '—' : `${item.average_mastery}%`,
        item.needs_review,
        item.developing,
        item.proficient
      ].forEach(value => row.append(element('td', '', String(value))));
      tbody.append(row);
    });
    table.append(caption, thead, tbody);
    tableWrap.append(table);
    fragment.append(tableWrap, element('p', 'privacy-note', `隐私模式：${data.privacy}`));
    dashboard.replaceChildren(fragment);
    announce('班级匿名概览已更新');
  } catch (error) {
    renderError(dashboard, error);
  }
}

function renderGrade(container, result) {
  const score = Math.round(result.score);
  const summary = element('div', 'grade-summary');
  const scoreRing = element('div', 'score-ring', `${score}`);
  scoreRing.style.setProperty('--score', score);
  scoreRing.setAttribute('aria-label', `本次得分 ${score} 分`);
  const copy = element('div', 'grade-copy');
  copy.append(
    element('strong', '', `本次得分 ${score} 分`),
    statusPill(result.mastery.status),
    element('p', '', result.feedback)
  );
  summary.append(scoreRing, copy);
  const list = element('ul', 'criterion-list');
  result.criterion_results.forEach(item => {
    const row = element('li', item.matched ? 'criterion-matched' : 'criterion-missing');
    row.append(
      element('span', 'criterion-icon', item.matched ? '✓' : '·'),
      element('span', '', `${item.label}${item.matched_keyword ? `（命中“${item.matched_keyword}”）` : '（待补充）'}`)
    );
    list.append(row);
  });
  container.replaceChildren(summary, list);
  container.focus();
  announce(`练习批改完成，得分 ${score} 分，掌握状态${masteryLabels[result.mastery.status]}`);
}

function createExerciseCard(exercise) {
  const card = element('section', 'exercise-card');
  card.dataset.exerciseId = exercise.exercise_id;
  const tag = element('span', 'exercise-tag', '可提交练习');
  const title = element('h3', '', exercise.knowledge_point_name);
  const question = element('p', 'exercise-question', exercise.question);
  const source = element('p', 'exercise-source', `出题依据：${exercise.citation.title}`);
  const answerForm = element('form', 'exercise-form');
  const answerId = `answer-${exercise.exercise_id}`;
  const label = element('label', '', '你的答案');
  label.htmlFor = answerId;
  const textarea = document.createElement('textarea');
  textarea.id = answerId;
  textarea.rows = 5;
  textarea.required = true;
  textarea.placeholder = '请按顺序写出关键步骤，并说明安全或调试要点…';
  const helpId = `help-${exercise.exercise_id}`;
  const help = element('p', 'field-help', '提交后会显示逐项评分依据，并更新“我的进度”。');
  help.id = helpId;
  textarea.setAttribute('aria-describedby', helpId);
  const submit = element('button', 'primary-button', '提交并批改');
  submit.type = 'submit';
  const result = element('div', 'grade-result');
  result.tabIndex = -1;
  result.setAttribute('aria-live', 'polite');
  answerForm.append(label, textarea, help, submit, result);
  answerForm.addEventListener('submit', async event => {
    event.preventDefault();
    const answer = textarea.value.trim();
    if (!answer) return;
    submit.disabled = true;
    submit.textContent = '正在批改…';
    textarea.setAttribute('aria-busy', 'true');
    try {
      const graded = await api(`/api/v1/exercises/${exercise.exercise_id}/submit`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-User-ID': userId},
        body: JSON.stringify({answer})
      });
      textarea.disabled = true;
      submit.hidden = true;
      help.textContent = '本次答案已经保存；每个练习只批改一次。';
      renderGrade(result, graded);
    } catch (error) {
      const alert = element('div', 'inline-alert', error.message);
      alert.setAttribute('role', 'alert');
      result.replaceChildren(alert);
      submit.disabled = false;
      submit.textContent = '重新提交';
      announce(error.message);
    } finally {
      textarea.removeAttribute('aria-busy');
    }
  });
  card.append(tag, title, question, source, answerForm);
  return card;
}

async function appendGeneratedExercise(exerciseId) {
  const data = await api(`/api/v1/students/${encodeURIComponent(userId)}/exercises`, {
    headers: {'X-User-ID': userId}
  });
  const exercise = data.items.find(item => item.exercise_id === exerciseId);
  if (!exercise) throw new Error('练习已生成，但暂时无法读取练习详情');
  const card = createExerciseCard(exercise);
  conversation.append(card);
  conversation.scrollTop = conversation.scrollHeight;
  card.querySelector('textarea').focus();
  announce('练习已生成，可以在当前页面填写并提交');
}

async function sendMessage(message) {
  addMessage('user', message);
  tracePanel.hidden = false;
  traceSteps.innerHTML = '';
  traceStatus.textContent = '处理中';
  const pending = addMessage('assistant', '正在检查课程资料…');
  const run = await api('/api/v1/chat', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session_id: sessionId, user_id: userId, message})
  });
  await new Promise((resolve, reject) => {
    let closed = false;
    const stream = new EventSource(run.stream_url);
    ['intent.classified', 'query.normalized', 'context.checked', 'tools.planned', 'tool.finished',
      'evidence.judged', 'safety.checked', 'clarification.requested', 'answer.completed',
      'answer.abstained', 'teacher.escalated'].forEach(type => {
      stream.addEventListener(type, () => {
        const label = traceLabel(type);
        if (label) traceSteps.append(element('span', '', label));
      });
    });
    stream.addEventListener('stream.closed', async event => {
      closed = true;
      const state = JSON.parse(event.data);
      stream.close();
      try {
        const data = await api(`/api/v1/runs/${run.run_id}`, {
          headers: {'X-User-ID': userId}
        });
        pending.textContent = data.answer || '本次处理没有生成回答。';
        traceStatus.textContent = state.status;
        if (data.generated_exercise_id) await appendGeneratedExercise(data.generated_exercise_id);
        announce(`任务处理完成，状态${state.status}`);
        resolve();
      } catch (error) { reject(error); }
    });
    stream.onerror = () => {
      stream.close();
      if (!closed) reject(new Error('过程流连接中断'));
    };
  });
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message) return;
  input.value = '';
  const button = form.querySelector('button');
  button.disabled = true;
  try {
    await sendMessage(message);
  } catch (error) {
    const bubble = addMessage('assistant', `暂时无法完成请求：${error.message}`);
    bubble.setAttribute('role', 'alert');
    announce(error.message);
  } finally {
    button.disabled = false;
    input.focus();
  }
});

document.querySelectorAll('.suggestions button').forEach(button => {
  button.addEventListener('click', () => {
    showChat(button.textContent);
  });
});

document.querySelectorAll('.nav-item').forEach(button => {
  button.addEventListener('click', () => {
    setActiveNavigation(button);
    const view = button.dataset.view;
    if (view === 'progress') renderStudentProgress();
    else if (view === 'teacher') renderTeacherSummary();
    else showChat(button.dataset.prompt || '');
  });
});

api('/ready')
  .then(data => {
    serviceStatus.lastChild.textContent = ` 服务在线 · ${data.indexed_chunks} 个片段`;
  })
  .catch(() => {
    serviceStatus.classList.add('offline');
    serviceStatus.lastChild.textContent = ' 服务暂不可用';
  });
