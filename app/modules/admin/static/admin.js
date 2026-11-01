"use strict";
const $ = id => document.getElementById(id);
let token = "", locationId = "", objectId = "", offset = 0, venueOffset = 0, venueCount = 0, rows = [], generation = 0;
let previewUrl = "", photoUrls = [], busy = false;
function status(message) { $("status").textContent = message; }
function lock() {
  token = ""; generation++; objectId = ""; locationId = "";
  clearImages(); $("detail").replaceChildren(); $("objects").replaceChildren();
  $("login").hidden = false; $("workspace").hidden = true; $("logout").hidden = true;
  $("token").value = ""; $("token").focus();
}
async function api(path, options = {}) {
  const response = await fetch(`/api/v1/admin${path}`, {
    ...options, headers: { Authorization: `Bearer ${token}` }, cache: "no-store"
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) lock();
    throw new Error(data.error?.message || "Request failed. Please retry.");
  }
  return response.status === 204 ? null : response.json();
}
function base() { return `/venues/${locationId}/objects/${objectId}`; }
function clearImages() {
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = "";
  photoUrls.forEach(url => URL.revokeObjectURL(url)); photoUrls = [];
}
async function venues() {
  const items = await api(`/venues?offset=${venueOffset}`);
  venueCount = items.length;
  $("venue").replaceChildren(...items.map(item => {
    const option = document.createElement("option"); option.value = item.id;
    option.textContent = `${item.name}${item.city ? ` · ${item.city}` : ""}`; return option;
  }));
  $("previousVenues").disabled = venueOffset === 0;
  $("nextVenues").disabled = items.length < 100;
  locationId = items[0]?.id || ""; offset = 0;
  if (!locationId) { $("objects").textContent = "No venues on this page."; return; }
  await inventory();
}
async function inventory(keepObject = false) {
  if (!locationId) return;
  const current = ++generation;
  const data = await api(`/venues/${locationId}/objects?offset=${offset}&limit=50`);
  if (current !== generation) return;
  rows = data.objects;
  $("previous").disabled = offset === 0; $("next").disabled = rows.length < 50;
  $("page").textContent = `Page ${offset / 50 + 1}`;
  if (!keepObject) {
    objectId = ""; clearImages(); $("detail").textContent = "Select an object to view photos and upload.";
  }
  renderObjects();
}
function renderObjects() {
  const query = $("search").value.toLowerCase();
  const visible = rows.filter(row => (!$("gaps").checked || row.needs_photos) && `${row.name} ${row.type}`.toLowerCase().includes(query));
  $("summary").textContent = `${visible.length} shown · ${rows.filter(row => row.needs_photos).length} need photos on this page. Targets are collection goals, not tested accuracy.`;
  $("objects").replaceChildren();
  for (const row of visible) {
    const button = document.createElement("button"); button.className = "objectCard";
    button.setAttribute("aria-pressed", String(row.id === objectId));
    const content = document.createElement("span"), name = document.createElement("strong"), description = document.createElement("small"), badge = document.createElement("span");
    name.textContent = row.name;
    description.textContent = `${row.type} · ${row.prepared} prepared · ${row.pending} saved for preparation`;
    badge.className = `badge${row.needs_photos ? "" : " complete"}`;
    badge.textContent = row.missing ? `${row.missing} more to target ${row.target}` : `Target ${row.target} collected`;
    content.append(name, description); button.append(content, badge);
    button.addEventListener("click", () => { if (!busy) showObject(row.id).catch(error => status(error.message)); });
    $("objects").append(button);
  }
  if (!visible.length) $("objects").textContent = "No objects match these filters. Turn off the photo-gap filter to see the full page.";
}
async function showObject(id) {
  const current = ++generation; objectId = id;
  const path = base(), data = await api(path);
  if (current !== generation) return;
  clearImages(); renderObjects();
  $("detail").replaceChildren($("detailTemplate").content.cloneNode(true));
  $("objectName").textContent = data.name;
  $("coverage").textContent = `${data.prepared} prepared · ${data.pending} saved · collection target ${data.target}`;
  $("stale").hidden = !data.stale;
  for (const view of data.views) {
    const item = document.createElement("li"); item.textContent = `${view.viewpoint} · rights ${view.rights_status}`; $("views").append(item);
  }
  if (!data.views.length) $("views").textContent = "No prepared references yet.";
  for (const source of data.sources) {
    const option = document.createElement("option"); option.value = source.id; option.textContent = source.title; $("source").append(option);
  }
  if (!data.sources.length) status("Link a canonical source to this object before adding reference photos.");
  for (const photo of data.photos) {
    const card = document.createElement("div"); card.className = "photo";
    const img = document.createElement("img"); img.alt = photo.viewpoint;
    const text = document.createElement("p"); text.textContent = `${photo.viewpoint} · ${photo.photographer} · ${photo.capture_group}`;
    const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Remove unpublished upload";
    remove.addEventListener("click", () => action(async () => {
      await api(`${path}/photos/${photo._id}`, { method: "DELETE" });
      await inventory(true); await showObject(id); status("Unpublished photo removed.");
    }));
    card.append(img, text, remove); $("photos").append(card);
    fetch(`/api/v1/admin${path}/photos/${photo._id}`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" }).then(async response => {
      if (!response.ok) throw new Error("Photo preview unavailable.");
      const blob = await response.blob();
      if (current !== generation) return;
      const url = URL.createObjectURL(blob); photoUrls.push(url); img.src = url;
    }).catch(error => { if (current === generation) status(error.message); });
  }
  $("prepare").disabled = !data.pending || data.stale;
  $("prepare").addEventListener("click", () => action(async () => {
    status("Preparing local image descriptors. Please keep this page open…");
    await api(`${path}/prepare`, { method: "POST" });
    await inventory(true); await showObject(id); status("References prepared. Revalidate recognition before relying on this reference set.");
  }));
  $("image").addEventListener("change", () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    const file = $("image").files[0]; $("preview").hidden = !file;
    if (file) { previewUrl = URL.createObjectURL(file); $("preview").src = previewUrl; }
  });
  $("uploadForm").addEventListener("submit", event => {
    event.preventDefault();
    const body = new FormData(event.target);
    const file = body.get("image");
    if (!file || file.size > 5 * 1024 * 1024) { status("Select a photo up to 5 MiB."); return; }
    action(async () => {
      await api(`${path}/photos`, { method: "POST", body });
      await inventory(true); await showObject(id); status("Photo saved privately. Use Prepare references to add it to recognition.");
    });
  });
}
async function action(work) {
  if (busy) return;
  busy = true;
  const controls = [...document.querySelectorAll("button,input,select,textarea")].map(el => [el, el.disabled]);
  controls.forEach(([el]) => { el.disabled = true; });
  try { await work(); } catch (error) { status(error.message); }
  finally {
    busy = false;
    controls.forEach(([el, disabled]) => { if (el.isConnected) el.disabled = disabled; });
    $("previous").disabled = offset === 0; $("next").disabled = rows.length < 50;
    $("previousVenues").disabled = venueOffset === 0; $("nextVenues").disabled = venueCount < 100;
  }
}
$("loginForm").addEventListener("submit", event => {
  event.preventDefault(); token = $("token").value.trim(); $("token").value = "";
  action(async () => { await venues(); $("login").hidden = true; $("workspace").hidden = false; $("logout").hidden = false; status(""); });
});
$("logout").addEventListener("click", () => { lock(); status("Panel locked."); });
$("venue").addEventListener("change", () => { locationId = $("venue").value; offset = 0; inventory().catch(error => status(error.message)); });
$("search").addEventListener("input", renderObjects); $("gaps").addEventListener("change", renderObjects);
$("refresh").addEventListener("click", () => action(() => inventory()));
$("previous").addEventListener("click", () => action(async () => { offset = Math.max(0, offset - 50); await inventory(); }));
$("next").addEventListener("click", () => action(async () => { offset += 50; await inventory(); }));
$("previousVenues").addEventListener("click", () => action(async () => { venueOffset = Math.max(0, venueOffset - 100); await venues(); }));
$("nextVenues").addEventListener("click", () => action(async () => { venueOffset += 100; await venues(); }));
