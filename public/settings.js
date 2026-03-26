const form = document.getElementById("settings-form");
const loadBtn = document.getElementById("load-btn");
const saveBtn = document.getElementById("save-btn");
const statusEl = document.getElementById("status");
const metaEl = document.getElementById("settings-meta");
const tipEl = document.getElementById("settings-tip");

const fields = {
  adminToken: document.getElementById("adminToken"),
  openaiApiKey: document.getElementById("openaiApiKey"),
  clearOpenaiApiKey: document.getElementById("clearOpenaiApiKey"),
  openaiBaseUrl: document.getElementById("openaiBaseUrl"),
  openaiModel: document.getElementById("openaiModel"),
  localApiKey: document.getElementById("localApiKey"),
  clearLocalApiKey: document.getElementById("clearLocalApiKey"),
  localBaseUrl: document.getElementById("localBaseUrl"),
  localModel: document.getElementById("localModel"),
  tavilyApiKey: document.getElementById("tavilyApiKey"),
  clearTavilyApiKey: document.getElementById("clearTavilyApiKey"),
  serpApiKey: document.getElementById("serpApiKey"),
  clearSerpApiKey: document.getElementById("clearSerpApiKey")
};

const CLEAR_SENTINEL = "__CLEAR__";

function setStatus(text) {
  statusEl.textContent = text;
}

function getToken() {
  const token = fields.adminToken.value.trim();
  if (!token) {
    throw new Error("请先输入 Admin Token");
  }
  return token;
}

function authHeaders() {
  return {
    "Content-Type": "application/json",
    "X-Admin-Token": getToken()
  };
}

function updateMeta(data) {
  metaEl.textContent = JSON.stringify(data, null, 2);
  tipEl.classList.remove("empty");
  tipEl.textContent = [
    `OpenAI Key: ${data.hasOpenaiApiKey ? "已配置" : "未配置"}`,
    `Local Key: ${data.hasLocalApiKey ? "已配置" : "未配置"}`,
    `Tavily Key: ${data.hasTavilyApiKey ? "已配置" : "未配置"}`,
    `SerpAPI Key: ${data.hasSerpApiKey ? "已配置" : "未配置"}`
  ].join("\n");
}

async function loadSettings() {
  setStatus("读取中...");
  const response = await fetch("/api/settings", {
    method: "GET",
    headers: {
      "X-Admin-Token": getToken()
    }
  });

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data?.detail || "读取失败");
  }

  fields.openaiBaseUrl.value = data.openaiBaseUrl || "";
  fields.openaiModel.value = data.openaiModel || "";
  fields.localBaseUrl.value = data.localBaseUrl || "";
  fields.localModel.value = data.localModel || "";

  updateMeta(data);
  setStatus("读取完成");
}

function buildPayload() {
  return {
    openaiApiKey: fields.clearOpenaiApiKey.checked ? CLEAR_SENTINEL : fields.openaiApiKey.value || null,
    openaiBaseUrl: fields.openaiBaseUrl.value || null,
    openaiModel: fields.openaiModel.value || null,
    localApiKey: fields.clearLocalApiKey.checked ? CLEAR_SENTINEL : fields.localApiKey.value || null,
    localBaseUrl: fields.localBaseUrl.value || null,
    localModel: fields.localModel.value || null,
    tavilyApiKey: fields.clearTavilyApiKey.checked ? CLEAR_SENTINEL : fields.tavilyApiKey.value || null,
    serpApiKey: fields.clearSerpApiKey.checked ? CLEAR_SENTINEL : fields.serpApiKey.value || null
  };
}

function clearSecretInputs() {
  fields.openaiApiKey.value = "";
  fields.localApiKey.value = "";
  fields.tavilyApiKey.value = "";
  fields.serpApiKey.value = "";
}

async function saveSettings() {
  setStatus("保存中...");
  const response = await fetch("/api/settings", {
    method: "PUT",
    headers: authHeaders(),
    body: JSON.stringify(buildPayload())
  });

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data?.detail || "保存失败");
  }

  clearSecretInputs();
  updateMeta(data);
  setStatus("保存完成");
}

loadBtn.addEventListener("click", async () => {
  try {
    await loadSettings();
  } catch (error) {
    setStatus("失败");
    tipEl.textContent = `读取失败：${error.message}`;
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  saveBtn.disabled = true;

  try {
    await saveSettings();
  } catch (error) {
    setStatus("失败");
    tipEl.textContent = `保存失败：${error.message}`;
  } finally {
    saveBtn.disabled = false;
  }
});
