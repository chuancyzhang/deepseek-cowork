"use strict";

const capabilityKinds = {skill:"Skill", mcp:"MCP", expert:"专家"};
const capabilityApi = "/admin/capabilities";
const capabilityPath = "/app/capabilities/";
const capabilityZipLimit = 4 * 1024 * 1024;
const capabilityUsesZip = kind => kind === "skill" || kind === "expert";

async function capabilityRoute(route) {
  const [, , , category, key, view] = route.split("/");
  const kind = category || "skill";
  if (!Object.hasOwn(capabilityKinds, kind)) throw new Error("请选择有效的能力类型。");
  if (!key) return capabilityList(kind);
  const item = key === "new" ? null : await api(capabilityApi + "/" + encodeURIComponent(key));
  if (item && item.kind !== kind) throw new Error("能力类型已变更，请返回列表。");
  if (item && view === "grants") return capabilityGrants(item);
  if (capabilityUsesZip(kind) && (!item || view === "upload")) return capabilityUpload(kind, item);
  return capabilityEditor(kind, item);
}

async function capabilityList(kind) {
  const {items} = await api(capabilityApi);
  heading("企业能力", "发布 Skill、MCP 和专家，员工获得授权后可在客户端使用。");
  content.querySelector(".page-title").append(button(capabilityUsesZip(kind) ? "上传 ZIP" : "新建 MCP",
    () => navigate(capabilityPath + kind + "/new"), {primary:true}));
  content.insertAdjacentHTML("beforeend", `<div class="space-tabs" aria-label="企业能力类型">
    ${Object.entries(capabilityKinds).map(([key, label]) =>
      `<a data-route href="${base + capabilityPath + key}" ${kind === key ? 'aria-current="page"' : ""}>${label}</a>`).join("")}
    </div>`);
  const rows = items.filter(item => item.kind === kind);
  if (!rows.length) {
    content.insertAdjacentHTML("beforeend", empty(capabilityUsesZip(kind)
      ? `暂无 ${capabilityKinds[kind]}。上传 ZIP 后会生成草稿。` : "暂无 MCP，请先添加服务配置。"));
    return;
  }
  content.insertAdjacentHTML("beforeend", `<div class="table-wrap"><table><thead><tr>
    <th>名称</th><th>状态</th><th>使用范围</th><th>操作</th></tr></thead>
    <tbody>${rows.map(item => `<tr><td><a data-route href="${base + capabilityPath + kind + "/" + esc(item.id)}">${esc(item.name)}</a>
    <span class="secondary-line">${esc(item.description)}</span></td>
    <td>${item.version ? (item.enabled ? "已发布" : "已停用") + " · v" + item.version : "未发布草稿"}</td>
    <td>${capabilityGrantLabel(item)}</td>
    <td><a data-route href="${base + capabilityPath + kind + "/" + esc(item.id)}/grants">分配员工</a></td></tr>`).join("")}
    </tbody></table></div>`);
}

function capabilityUpload(kind, item) {
  const page = capabilityPath + kind;
  const label = capabilityKinds[kind];
  const structure = kind === "skill"
    ? "ZIP 应包含 SKILL.md，可放在根目录或单一外层目录。参考资料、脚本和其他文本资源可分别放在 references/、scripts/、assets/。"
    : "ZIP 根目录需有 expert.json 和 prompt.md。请在 skills/ 下放入 1–8 个 Skill，每个使用单独目录。";
  heading(item ? `上传新版 ${label} ZIP` : `上传 ${label} ZIP`,
    item ? `${item.name} · 上传只更新草稿，已发布版本仍可使用。` : "文件上传后保存为草稿，检查后即可发布。",
    item ? `${page}/${item.id}` : page);
  if (item) content.querySelector(".page-title a").textContent = "返回能力配置";
  content.insertAdjacentHTML("beforeend", `<form class="form" id="capability-zip-form">
    <label>${label} ZIP<input type="file" name="archive" accept=".zip,application/zip" required></label>
    <p class="help">${structure} 支持 UTF-8 文本，ZIP 最大 4 MiB。</p>
    ${kind === "expert" ? '<p class="help">随包导入的 Skill 会单独保存为草稿。请先发布并授权这些 Skill，再发布和分配专家。</p>' : ""}
    <div class="actions-row"><button class="primary" type="submit">上传并保存草稿</button>
    <a href="${base}/api/v1${capabilityApi}/zip-template/${kind}" download>下载 ZIP 模板</a></div>
    <p id="capability-zip-status" role="status"></p></form>`);
  const form = document.getElementById("capability-zip-form");
  const fileInput = form.elements.archive, submit = form.querySelector('button[type="submit"]');
  const status = document.getElementById("capability-zip-status");
  watchForm(form);
  fileInput.addEventListener("change", () => { status.textContent = ""; });
  form.onsubmit = async event => {
    event.preventDefault();
    const zip = fileInput.files[0];
    if (!zip) return;
    if (zip.size > capabilityZipLimit) {
      status.textContent = "ZIP 超过 4 MiB，请减小压缩包后重试。";
      status.className = "danger";
      return;
    }
    const generation = routeGeneration;
    const endpoint = `${base}/api/v1${capabilityApi}/zip/${kind}${item ? `/${item.id}?revision=${item.revision}` : ""}`;
    submit.disabled = fileInput.disabled = true;
    status.textContent = "正在上传并检查 ZIP…";
    status.className = "muted";
    try {
      let response;
      try {
        response = await fetch(endpoint, {method:item ? "PUT" : "POST", credentials:"same-origin", body:zip,
          headers:{"Content-Type":"application/zip", "X-CSRF-Token":csrf()}});
      } catch {
        throw new Error("上传连接中断。文件选择已保留，请先查看列表确认草稿是否已保存。");
      }
      const result = await response.json().catch(() => ({}));
      if (generation !== routeGeneration) return;
      if (!response.ok) throw new Error(result.error?.message || `上传失败（${response.status}），文件选择已保留。`);
      dirty = false;
      history.replaceState({}, "", `${base}${page}/${result.id}`);
      await render();
      message(result.bundled_skills?.length ? `专家草稿已保存，已关联 ${result.bundled_skills.length} 个随包 Skill。` : "ZIP 已导入草稿。", "success");
    } catch(error) {
      if (form.isConnected) {
        status.textContent = error.message;
        status.className = "danger";
      } else if (error.name !== "AbortError" && generation + 1 === routeGeneration) message(error.message, "error");
    } finally {
      if (form.isConnected) submit.disabled = fileInput.disabled = false;
    }
  };
}

function capabilityGrantLabel(item) {
  return item.all_members ? "全部成员" : item.member_ids.length ? `已选 ${item.member_ids.length} 人` : "未分配";
}

function capabilityFields(kind, item, catalog, models) {
  const config = item?.config || {};
  if (kind === "mcp") {
    return `<label>服务地址<input name="url" type="url" maxlength="2048" value="${esc(config.url)}" placeholder="https://example.internal/mcp"></label>
      <p class="help">连接方式为 Streamable HTTP。请使用员工电脑可访问的地址。</p>
      <label>成员认证<select name="auth_type"><option value="none">无需认证</option>
      <option value="bearer" ${config.auth_type === "bearer" ? "selected" : ""}>Bearer 凭据</option></select></label>
      <label id="capability-credential">凭据名称
      <input name="credential_variable" autocomplete="off" maxlength="128" value="${esc(config.credential_variable)}">
      <span class="help">员工在桌面端的企业 MCP 标签页为此服务填写访问令牌。这里仅填写名称。</span></label>
      <label>允许使用的工具<textarea name="allowed_tools" rows="3" placeholder="每行一个工具名称，留空允许全部工具">${esc((config.allowed_tools || []).join("\n"))}</textarea></label>`;
  }
  const instructions = `<label>工作指引<textarea name="instructions" rows="6" spellcheck="false">${esc(config.instructions)}</textarea></label>`;
  if (kind === "skill") {
    return instructions + `<details${Object.keys(config.files || {}).length ? " open" : ""}><summary>脚本和文本资源</summary>
      <label>文件路径与内容（JSON）<textarea class="code" name="files" spellcheck="false" placeholder='{"references/说明.md":"文件内容"}'>${esc(Object.keys(config.files || {}).length ? JSON.stringify(config.files, null, 2) : "")}</textarea></label>
      <p class="help">支持 references/、scripts/ 和 assets/ 目录。文件内容为文本，合计最多 384 KiB。</p></details>`;
  }
  const selectedModel = config.default_model_id || "";
  const candidates = models.filter(model => model.enabled || model.id === selectedModel);
  const choice = dependencyKind => {
    const selected = new Set(config[dependencyKind + "_ids"] || []);
    const options = catalog.filter(candidate => candidate.kind === dependencyKind
      && ((candidate.enabled && candidate.version) || selected.has(candidate.id)));
    if (!options.length) return "";
    return `<fieldset class="capability-dependencies"><legend>关联 ${capabilityKinds[dependencyKind]}</legend><div class="check-list">${options.map(candidate =>
      `<label><input type="checkbox" name="${dependencyKind}_ids" value="${esc(candidate.id)}" ${selected.has(candidate.id) ? "checked" : ""}>
      ${esc(candidate.published_name || candidate.name)}${candidate.enabled ? "" : candidate.version ? "（已停用）" : "（待发布）"}</label>`).join("")}</div></fieldset>`;
  };
  return instructions + `<label>默认模型<select name="default_model_id"><option value="">由员工选择</option>
    ${candidates.map(model => `<option value="${esc(model.id)}" ${model.id === selectedModel ? "selected" : ""}>${esc(model.name)}${model.enabled ? "" : "（已停用）"}</option>`).join("")}
    </select></label>` + choice("skill") + choice("mcp");
}

function capabilityConfig(kind, data, form) {
  const values = new FormData(form);
  if (kind === "skill") {
    let files;
    try { files = JSON.parse(data.files.trim() || "{}"); } catch { throw new Error("脚本和文本资源不是有效的 JSON，请检查后再保存。"); }
    return {instructions:data.instructions, files};
  }
  if (kind === "mcp") {
    return {url:data.url, transport:"streamable_http", auth_type:data.auth_type,
      credential_variable:data.auth_type === "bearer" ? data.credential_variable : "",
      allowed_tools:data.allowed_tools.split(/\r?\n/).map(value => value.trim()).filter(Boolean)};
  }
  return {instructions:data.instructions, default_model_id:data.default_model_id,
    skill_ids:values.getAll("skill_ids"), mcp_ids:values.getAll("mcp_ids")};
}

async function capabilityEditor(kind, item) {
  const [catalog, models] = kind === "expert"
    ? await Promise.all([api(capabilityApi), api("/admin/models")]) : [{items:[]}, {items:[]}];
  const page = capabilityPath + kind;
  heading(item?.name || "新建 " + capabilityKinds[kind], item?.version
    ? `${item.enabled ? "已发布" : "已停用"} v${item.version}${item.draft_pending ? " · 有未发布草稿" : ""} · ${capabilityGrantLabel(item)}`
    : "保存草稿后检查配置，再发布给员工。", page);
  if (item && capabilityUsesZip(kind)) {
    const upload = document.createElement("a");
    upload.href = `${base}${page}/${item.id}/upload`;
    upload.dataset.route = "";
    upload.textContent = "上传新版 ZIP";
    content.querySelector(".page-title").append(upload);
  }
  const pending = kind === "expert" && item ? catalog.items.filter(candidate => candidate.kind === "skill" && !candidate.enabled
    && (item.config.skill_ids || []).includes(candidate.id)) : [];
  if (pending.length) {
    content.insertAdjacentHTML("beforeend", `<div class="note">请先发布或恢复关联的 Skill，才能检查并发布此专家。
      <ul>${pending.map(skill => `<li><a data-route href="${base}${capabilityPath}skill/${esc(skill.id)}">${esc(skill.name)}</a></li>`).join("")}</ul></div>`);
  }
  content.insertAdjacentHTML("beforeend", `<form class="form" id="capability-form">
    <label>名称<input required name="name" maxlength="128" value="${esc(item?.name)}"></label>
    <label>说明<input name="description" maxlength="2000" value="${esc(item?.description)}"></label>
    ${capabilityFields(kind, item, catalog.items, models.items)}
    <div class="actions-row" id="capability-actions"><button class="primary" type="submit">保存草稿</button></div>
    <span id="capability-status" class="muted">${item?.tested ? "已保存的配置已通过检查。" : ""}</span>
    </form>`);
  const form = document.getElementById("capability-form");
  watchForm(form);
  if (kind === "mcp") {
    const updateCredential = () => {
      const bearer = form.elements.auth_type.value === "bearer";
      document.getElementById("capability-credential").hidden = !bearer;
      form.elements.credential_variable.required = bearer;
    };
    form.elements.auth_type.addEventListener("change", updateCredential);
    updateCredential();
  }
  const ops = [];
  const submit = form.querySelector('button[type="submit"]');
  const updateActions = busy => {
    submit.disabled = busy || (!!item && !dirty);
    const primary = !item || dirty ? submit
      : ops.find(entry => entry.name === (item.tested ? "publish" : "test") && !entry.unavailable)?.element;
    submit.classList.toggle("primary", primary === submit);
    for (const entry of ops) {
      entry.element.disabled = busy || dirty || entry.unavailable;
      entry.element.classList.toggle("primary", primary === entry.element);
    }
    for (const field of form.querySelectorAll("input,select,textarea")) field.disabled = busy;
  };
  const mutate = async (name, success) => {
    updateActions(true);
    try {
      await api(capabilityApi + "/" + item.id + "/" + name, {method:"POST", body:{revision:item.revision}});
      await render();
      message(success, "success");
    } finally { if (form.isConnected) updateActions(false); }
  };
  const addAction = (name, label, callback, unavailable = false, style = {}) => {
    const element = button(label, callback, style);
    ops.push({name, element, unavailable});
    document.getElementById("capability-actions").append(element);
  };
  if (item) {
    addAction("test", "检查配置", () => mutate("test", kind === "mcp"
      ? "配置检查通过。请在桌面端确认服务可访问，并使用已授权的工具。" : "配置检查通过。"));
    addAction("publish", item.enabled || !item.version || item.draft_pending ? "发布版本" : "恢复使用",
      () => mutate("publish", "企业能力已发布。"), !item.tested || (item.enabled && !item.draft_pending));
    if (item.enabled) addAction("disable", "停用", async () => {
      if (confirm("停用后员工不能继续使用此企业能力。确认停用？")) await mutate("disable", "企业能力已停用。");
    }, false, {danger:true});
    content.insertAdjacentHTML("beforeend", `<p class="section"><a href="${base + page + "/" + esc(item.id)}/grants" data-route>分配员工</a></p>`);
    if (item.versions.length) {
      content.insertAdjacentHTML("beforeend", `<details class="section"><summary>发布记录</summary>
        <ul>${item.versions.map(version => `<li>v${version.version} · ${esc(version.name)} · ${date(version.published_at)}</li>`).join("")}</ul></details>`);
    }
  }
  form.addEventListener("input", () => updateActions(false));
  form.addEventListener("change", () => updateActions(false));
  updateActions(false);
  form.onsubmit = event => {
    event.preventDefault();
    return action(async () => {
      const data = formData(form);
      const payload = {kind, name:data.name, description:data.description, config:capabilityConfig(kind, data, form)};
      if (item) payload.revision = item.revision;
      updateActions(true);
      try {
        const result = await api(item ? capabilityApi + "/" + item.id : capabilityApi,
          {method:item ? "PUT" : "POST", body:payload});
        dirty = false;
        if (!item) history.replaceState({}, "", base + page + "/" + result.id);
        await render();
        message("草稿已保存。", "success");
      } finally { if (form.isConnected) updateActions(false); }
    });
  };
}

async function capabilityGrants(item) {
  const {items} = await api("/admin/members");
  heading("分配员工", item.name, capabilityPath + item.kind + "/" + item.id);
  content.querySelector(".page-title a").textContent = "返回能力配置";
  const options = items.filter(member => member.enabled || item.member_ids.includes(member.id));
  content.insertAdjacentHTML("beforeend", `<form class="form" id="capability-grants">
    <label>使用范围<select name="scope"><option value="selected">指定成员</option>
    <option value="all" ${item.all_members ? "selected" : ""}>全部成员</option></select></label>
    <div id="capability-members">
      <label>查找成员<input id="capability-member-search" placeholder="姓名或邮箱"></label>
      <div class="check-list">${options.map(member =>
        `<label data-candidate="${esc(member.name + " " + member.email)}"><input name="members" type="checkbox" value="${esc(member.id)}"
        ${item.member_ids.includes(member.id) ? "checked" : ""}>${esc(member.name)}${member.enabled ? "" : "（已停用）"}
        </label>`).join("") || "没有可选择的员工。"}
      </div>
    </div>
    <button class="primary" type="submit">保存授权</button></form>`);
  const form = document.getElementById("capability-grants");
  watchForm(form);
  const showCandidates = () => { document.getElementById("capability-members").hidden = form.elements.scope.value === "all"; };
  form.elements.scope.addEventListener("change", showCandidates);
  showCandidates();
  document.getElementById("capability-member-search").addEventListener("input", event => {
    const key = event.target.value.toLowerCase().trim();
    for (const row of form.querySelectorAll("[data-candidate]")) row.hidden = !row.dataset.candidate.toLowerCase().includes(key);
  });
  form.onsubmit = event => {
    event.preventDefault();
    return action(async () => {
      const all_members = form.elements.scope.value === "all";
      const member_ids = all_members ? [] : new FormData(form).getAll("members");
      const fields = form.querySelectorAll("input,select,button");
      for (const field of fields) field.disabled = true;
      try {
        await api(capabilityApi + "/" + item.id + "/grant",
          {method:"POST", body:{revision:item.revision, all_members, member_ids}});
        dirty = false;
        await render();
        message("授权已保存。", "success");
      } finally { for (const field of fields) field.disabled = false; }
    });
  };
}
