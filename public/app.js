const form = document.getElementById("research-form");
const submitBtn = document.getElementById("submit-btn");
const statusEl = document.getElementById("status");
const metaEl = document.getElementById("meta");
const reportEl = document.getElementById("report");
const streamLogEl = document.getElementById("stream-log");
const historyListEl = document.getElementById("history-list");
const refreshHistoryBtn = document.getElementById("refresh-history");

let currentEventSource = null;
let currentTaskId = null;

function setStatus(text) {
  statusEl.textContent = text;
}

function appendLog(message) {
  const line = `[${new Date().toLocaleTimeString()}] ${message}`;
  streamLogEl.textContent = streamLogEl.textContent === "尚未开始" ? line : `${streamLogEl.textContent}\n${line}`;
  streamLogEl.scrollTop = streamLogEl.scrollHeight;
}

function closeEventSource() {
  if (currentEventSource) {
    currentEventSource.close();
    currentEventSource = null;
  }
}

function renderMeta(task) {
  const meta = task?.result?.meta || {
    taskId: task?.id,
    status: task?.status,
    progress: task?.progress
  };
  metaEl.textContent = JSON.stringify(meta, null, 2);
}

function renderReport(task) {
  const report = task?.result?.report;
  if (report) {
    reportEl.classList.remove("empty");
    reportEl.textContent = report;
    return;
  }

  reportEl.classList.add("empty");
  reportEl.textContent = task?.error ? `任务失败：${task.error}` : "报告尚未生成";
}

function historyItemHtml(task) {
  const canDownload = Boolean(task.reportPath);
  const download = canDownload
    ? `<a href="/api/research/tasks/${task.id}/markdown" target="_blank" rel="noreferrer">导出 Markdown</a>`
    : "<span>未生成报告</span>";
  const mode = task.agentMode || "multi";

  return `
    <li>
      <div><strong>${task.topic}</strong></div>
      <div class="history-meta">${task.status} | ${mode} | ${task.provider} | ${task.searchProvider} | ${new Date(task.createdAt).toLocaleString()}</div>
      <div class="history-actions">
        <button data-task-id="${task.id}" type="button">查看</button>
        ${download}
      </div>
    </li>
  `;
}

async function loadTask(taskId) {
  const response = await fetch(`/api/research/tasks/${taskId}`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data?.error || "加载任务失败");
  }

  renderMeta(data.task);
  renderReport(data.task);
}

async function refreshHistory() {
  const response = await fetch("/api/research/tasks");
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data?.error || "加载历史失败");
  }

  const tasks = data.tasks || [];
  historyListEl.innerHTML = tasks.length ? tasks.map(historyItemHtml).join("") : "<li>暂无任务历史</li>";

  historyListEl.querySelectorAll("button[data-task-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      const { taskId } = button.dataset;
      currentTaskId = taskId;
      setStatus(`查看任务 ${taskId.slice(0, 8)}`);
      await loadTask(taskId);
    });
  });
}

async function startStream(taskId) {
  closeEventSource();
  currentEventSource = new EventSource(`/api/research/tasks/${taskId}/stream`);

  currentEventSource.addEventListener("snapshot", async (event) => {
    const data = JSON.parse(event.data);
    appendLog(`连接成功，当前状态 ${data.status}，进度 ${data.progress}%`);
    setStatus(`${data.status} (${data.progress}%)`);
    await loadTask(taskId);
  });

  currentEventSource.addEventListener("progress", async (event) => {
    const data = JSON.parse(event.data);
    appendLog(`${data.stage}: ${data.message} (${data.progress}%)`);
    setStatus(`${data.stage} (${data.progress}%)`);

    if (data.stage === "complete" || data.stage === "error") {
      await loadTask(taskId);
      await refreshHistory();
    }
  });

  currentEventSource.addEventListener("done", async (event) => {
    const data = JSON.parse(event.data);
    setStatus(data.status);
    appendLog(`任务结束：${data.status}`);
    await loadTask(taskId);
    await refreshHistory();
    closeEventSource();
    submitBtn.disabled = false;
  });

  currentEventSource.onerror = () => {
    appendLog("SSE 连接中断");
    setStatus("连接中断");
    closeEventSource();
    submitBtn.disabled = false;
  };
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const formData = new FormData(form);
  const payload = Object.fromEntries(formData.entries());
  const userSources = (payload.sources || "")
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);

  const requestBody = {
    topic: payload.topic,
    context: payload.context,
    depth: payload.depth,
    agentMode: payload.agentMode || "multi",
    provider: payload.provider,
    userSources,
    searchProvider: payload.searchProvider,
    searchMaxResults: Number(payload.searchMaxResults || 8)
  };

  submitBtn.disabled = true;
  streamLogEl.textContent = "尚未开始";
  reportEl.classList.remove("empty");
  reportEl.textContent = "任务已创建，等待实时输出...";
  metaEl.textContent = "";
  setStatus("提交中...");

  try {
    const response = await fetch("/api/research/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody)
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data?.detail || data?.error || "任务创建失败");
    }

    currentTaskId = data.taskId;
    appendLog(`任务已创建: ${data.taskId}`);
    setStatus(`queued (${data.taskId.slice(0, 8)})`);
    await refreshHistory();
    await startStream(data.taskId);
  } catch (error) {
    setStatus("失败");
    appendLog(`错误: ${error.message}`);
    reportEl.textContent = `执行失败：${error.message}`;
    submitBtn.disabled = false;
  }
});

refreshHistoryBtn.addEventListener("click", async () => {
  await refreshHistory();
});

appendLog("安全模式已启用：密钥从服务端配置中心读取。前往 /settings 可更新。");
refreshHistory().catch((error) => {
  appendLog(`历史加载失败: ${error.message}`);
});
