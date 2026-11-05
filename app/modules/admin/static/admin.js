"use strict";
const $ = id => document.getElementById(id);
let token = "", locationId = "", objectId = "", offset = 0, venueOffset = 0, venueCount = 0, rows = [], generation = 0;
let previewUrl = "", photoUrls = [], busy = false;
let capturePhotos = [], captureDraft = null, captureReading = false, captureStep = 1;
function status(message) { $("status").textContent = message; }
function lock() {
  token = ""; generation++; objectId = ""; locationId = "";
  resetCapture(); showCollection(); $("venueDialog").close(); $("captureObserver").value = "";
  clearImages(); $("detail").replaceChildren(); $("objects").replaceChildren();
  $("login").hidden = false; $("workspace").hidden = true; $("logout").hidden = true;
  $("token").value = ""; $("token").focus();
}
async function api(path, options = {}) {
  const response = await fetch(`/api/v1/admin${path}`, {
    ...options, headers: { ...options.headers, Authorization: `Bearer ${token}` }, cache: "no-store"
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
function objectVenue() {
  document.querySelectorAll("#objectForm input, #objectForm select, #objectForm textarea, #venue, #venueForm button").forEach(el => { el.disabled = busy || captureReading || !!captureDraft; });
  $("createObject").disabled = busy || captureReading || !locationId || !capturePhotos.length;
  $("takePhoto").disabled = $("choosePhotos").disabled = busy || captureReading || !!captureDraft || !locationId || capturePhotos.length >= 4;
  $("createObject").textContent = captureDraft ? "Retry upload" : "Save object";
  $("nextCapture").disabled = busy || captureReading || !locationId || !capturePhotos.length;
  $("captureBack").disabled = busy || !!captureDraft;
  $("openCapture").disabled = busy;
  $("openVenue").disabled = busy || !!captureDraft;
  $("objectVenue").textContent = locationId
    ? `Venue: ${$("venue").selectedOptions[0].textContent}`
    : "Choose a venue in your collection first.";
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
  objectVenue();
  if (!locationId) { $("objects").textContent = "No venues on this page."; return; }
  await inventory();
}
async function inventory(keepObject = false) {
  objectVenue();
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
  $("summary").textContent = `${visible.length} objects · ${rows.filter(row => row.needs_photos).length} need photos`;
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
  $("stale").hidden = !data.stale;
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
    await inventory(true); await showObject(id); status("Photos prepared.");
  }));
  $("image").addEventListener("change", () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    const file = $("image").files[0]; $("preview").hidden = !file;
    if (file) { previewUrl = URL.createObjectURL(file); $("preview").src = previewUrl; }
  });
  $("uploadForm").addEventListener("submit", event => {
    event.preventDefault();
    const body = new FormData(event.target);
    body.set("photographer", "Admin test upload");
    body.set("capture_group", `admin-test-${crypto.randomUUID()}`);
    body.set("rights", "Admin test upload; rights details not recorded.");
    const file = body.get("image");
    if (!file || file.size > 5 * 1024 * 1024) { status("Select a photo up to 5 MiB."); return; }
    action(async () => {
      await api(`${path}/photos`, { method: "POST", body });
      await inventory(true); await showObject(id); status("Photo saved.");
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
    $("previousVenues").disabled = !!captureDraft || venueOffset === 0; $("nextVenues").disabled = !!captureDraft || venueCount < 100;
    if (captureDraft) {
      $("captureProgress").textContent = "Some photos could not be uploaded. Your object and completed uploads are saved. Tap Retry upload.";
      $("finishLater").hidden = false;
      document.querySelectorAll("#objectForm input, #objectForm select, #objectForm textarea, #venue, #venueForm button").forEach(el => { el.disabled = true; });
    }
    objectVenue();
  }
}
$("loginForm").addEventListener("submit", event => {
  event.preventDefault(); token = $("token").value.trim(); $("token").value = "";
  action(async () => { await venues(); $("login").hidden = true; $("workspace").hidden = false; $("logout").hidden = false; status(""); });
});
$("logout").addEventListener("click", () => { lock(); status("Panel locked."); });
$("venue").addEventListener("change", () => { if (captureDraft) { $("venue").value = locationId; status("Finish uploading this object before changing venues."); return; } locationId = $("venue").value; offset = 0; inventory().catch(error => status(error.message)); });
$("search").addEventListener("input", renderObjects); $("gaps").addEventListener("change", renderObjects);
$("refresh").addEventListener("click", () => action(() => inventory()));
$("previous").addEventListener("click", () => action(async () => { offset = Math.max(0, offset - 50); await inventory(); }));
$("next").addEventListener("click", () => action(async () => { offset += 50; await inventory(); }));
$("previousVenues").addEventListener("click", () => action(async () => { venueOffset = Math.max(0, venueOffset - 100); await venues(); }));
$("nextVenues").addEventListener("click", () => action(async () => { venueOffset += 100; await venues(); }));

$("venueForm").addEventListener("submit", event => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.target));
  if (!data.official_website) delete data.official_website;
  action(async () => {
    const venue = await api("/venues", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
    const option = document.createElement("option");
    option.value = venue.id; option.textContent = `${venue.name} · ${venue.city}`;
    $("venue").append(option); $("venue").value = venue.id;
    locationId = venue.id; offset = 0;
    $("search").value = "";
    await inventory();
    $("venueForm").reset();
    $("venueDialog").close(); openCapture();
    status("Venue created. Add its first object.");
  });
});

function showCollection() {
  $("addObject").hidden = true; document.body.classList.remove("wizardOpen"); $("collection").hidden = false; $("collection").inert = false; $("siteHeader").inert = false;
  if (token && !busy) $("openCapture").focus({ preventScroll: true });
}
function openCapture() {
  status("");
  if (!locationId) { $("venueDialog").showModal(); return; }
  $("addObject").hidden = false; document.body.classList.add("wizardOpen"); $("collection").hidden = true; $("collection").inert = true; $("siteHeader").inert = true;
  showCaptureStep(captureDraft ? 2 : captureStep);
  $("addObject").scrollTop = 0;
}
function showCaptureStep(step) {
  if (captureDraft && step === 1) return;
  captureStep = step;
  $("photoStep").hidden = step !== 1; $("detailsStep").hidden = step !== 2;
  $("nextCapture").hidden = step !== 1; $("createObject").hidden = step !== 2;
  $("captureBack").textContent = step === 1 ? "Cancel" : "Back";
  $("stepCount").textContent = `${step} of 2`;
  $("detailsProgress").classList.toggle("active", step === 2);
  if (step === 2) {
    const summary = $("detailsPhotoSummary"); summary.replaceChildren();
    if (capturePhotos.length) {
      const image = document.createElement("img"); image.src = capturePhotos[0].url; image.alt = "Object photo";
      const text = document.createElement("span"); text.textContent = `${capturePhotos.length} photo${capturePhotos.length === 1 ? "" : "s"} selected`;
      const edit = document.createElement("button"); edit.type = "button"; edit.className = "textButton"; edit.textContent = "Edit"; edit.disabled = !!captureDraft;
      edit.addEventListener("click", () => showCaptureStep(1));
      summary.append(image, text, edit);
    }
  }
  $("addObject").scrollTop = 0;
  objectVenue();
  $(step === 1 ? "captureTitle" : "detailsTitle").focus({ preventScroll: true });
}
function nextCaptureStep() {
  if (captureReading || !capturePhotos.length) return;
  for (const select of $("capturePhotos").querySelectorAll("select")) if (!select.reportValidity()) return;
  showCaptureStep(2);
}
$("openCapture").addEventListener("click", openCapture);
$("closeCapture").addEventListener("click", showCollection);
document.addEventListener("keydown", event => { if (event.key === "Escape" && !busy && !$("addObject").hidden) showCollection(); });
$("captureBack").addEventListener("click", () => { if (captureStep === 1) showCollection(); else showCaptureStep(1); });
$("nextCapture").addEventListener("click", nextCaptureStep);
$("openVenue").addEventListener("click", () => $("venueDialog").showModal());
$("closeVenue").addEventListener("click", () => $("venueDialog").close());
function resetCapture() {
  captureStep = 1;
  $("finishLater").hidden = true;
  capturePhotos.forEach(photo => URL.revokeObjectURL(photo.url));
  capturePhotos = []; captureDraft = null;
  $("objectForm").reset();
  document.querySelectorAll("#objectForm input, #objectForm select, #objectForm textarea, #venue, #venueForm button").forEach(el => { el.disabled = busy; });
  $("captureProgress").textContent = "";
  renderCapture();
}
function renderCapture() {
  $("captureEmpty").hidden = capturePhotos.length > 0;
  $("capturePhotos").classList.toggle("single", capturePhotos.length === 1);
  document.querySelector(".captureActions").classList.toggle("compact", capturePhotos.length > 0);
  $("capturePhotos").replaceChildren();
  capturePhotos.forEach((photo, index) => {
    const card = document.createElement("div"); card.className = "capturePhoto";
    const image = document.createElement("img"); image.src = photo.url; image.alt = `Selected photo ${index + 1}`;
    const label = document.createElement("label"); label.textContent = "";
    const select = document.createElement("select"); select.required = true; select.setAttribute("aria-label", `Photo ${index + 1} angle`);
    for (const [value, text] of [["", "Choose angle"], ["front", "Front"], ["left", "Left side"], ["right", "Right side"], ["back", "Back"], ["detail", "Close-up"], ["other", "Other view"]]) {
      const option = document.createElement("option"); option.value = value; option.textContent = text; select.append(option);
    }
    select.value = photo.viewpoint;
    select.disabled = busy || photo.saved;
    select.addEventListener("change", () => { photo.viewpoint = select.value; });
    label.append(select);
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "removeCapture";
    remove.textContent = photo.saved ? "Saved" : "\u00d7";
    remove.setAttribute("aria-label", photo.saved ? `Photo ${index + 1} uploaded` : `Remove photo ${index + 1}`);
    remove.disabled = busy || !!captureDraft;
    remove.addEventListener("click", () => {
      URL.revokeObjectURL(photo.url); capturePhotos = capturePhotos.filter(item => item !== photo); renderCapture();
    });
    card.append(image, label, remove); $("capturePhotos").append(card);
  });
  $("captureCount").textContent = capturePhotos.length ? `${capturePhotos.length} of 4 photos added` : "No photos added yet";
  objectVenue();
}
async function optimizePhoto(file) {
  if (file.size > 40 * 1024 * 1024) throw new Error("This photo is too large. Choose an image under 40 MB.");
  const url = URL.createObjectURL(file), image = new Image();
  try {
    image.src = url;
    await image.decode();
    if (!image.naturalWidth || image.naturalWidth * image.naturalHeight > 64000000) throw new Error("Image too large");
    const scale = Math.min(1, 1800 / Math.max(image.naturalWidth, image.naturalHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale)); canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Image conversion unavailable");
    context.fillStyle = "white"; context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", 0.88));
    if (!blob || blob.size > 5 * 1024 * 1024) throw new Error("Image conversion failed");
    return new File([blob], "reference.jpg", { type: "image/jpeg" });
  } catch {
    throw new Error("Couldn't open this photo. Try taking a new photo or choose a JPEG, PNG or WebP image.");
  } finally { URL.revokeObjectURL(url); }
}
async function addCapturePhotos(files) {
  if (busy || captureReading || captureDraft) return;
  captureReading = true; objectVenue();
  const session = generation;
  let message = "";
  try {
    for (const file of files) {
      if (capturePhotos.length >= 4) { message = "Up to 4 photos per object. Remove a photo to choose another."; break; }
      $("captureProgress").textContent = "Optimizing photo on your device...";
      try {
        const optimized = await optimizePhoto(file);
        const digest = await crypto.subtle.digest("SHA-256", await optimized.arrayBuffer());
        const sha = [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, "0")).join("");
        if (session !== generation) break;
        if (capturePhotos.some(photo => photo.sha === sha)) { message = "That photo is already selected. Try another angle."; continue; }
        capturePhotos.push({ file: optimized, url: URL.createObjectURL(optimized), sha, viewpoint: capturePhotos.length ? "" : "front", saved: false });
      } catch (error) { message = error.message; }
    }
  } finally {
    captureReading = false; $("captureProgress").textContent = message;
    renderCapture();
  }
}
$("takePhoto").addEventListener("click", () => $("cameraInput").click());
$("choosePhotos").addEventListener("click", () => $("libraryInput").click());
for (const id of ["cameraInput", "libraryInput"]) $(id).addEventListener("change", event => {
  const files = [...event.target.files]; event.target.value = ""; addCapturePhotos(files);
});
$("objectForm").addEventListener("submit", event => {
  event.preventDefault();
  if (captureStep === 1) { nextCaptureStep(); return; }
  if (!captureDraft && !event.target.reportValidity()) return;
  if (!locationId || !capturePhotos.length || captureReading) return;
  const fields = Object.fromEntries([...new FormData(event.target)].map(([key, value]) => [key, value.trim()]).filter(([, value]) => value));
  const observer = $("captureObserver").value.trim(), rights = $("captureRights").value.trim();
  if (!observer || !rights || !$("captureReviewed").checked) { status("Enter your name, photo permission and confirm the object's identity."); return; }
  const venueId = captureDraft?.venueId || locationId;
  action(async () => {
    if (!captureDraft) {
      $("captureProgress").textContent = "Saving object...";
      const object = await api(`/venues/${venueId}/objects`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...fields, observation: { observer, reviewed: true } }) });
      captureDraft = { venueId, object, observer, rights, captureGroup: `admin-${crypto.randomUUID()}` };
    }
    const draft = captureDraft, path = `/venues/${draft.venueId}/objects/${draft.object.id}`;
    // Reconcile a response lost after a successful upload before retrying it.
    const existing = await api(path);
    for (const photo of capturePhotos) if (existing.photos.some(saved => saved.sha256 === photo.sha && saved.capture_group === draft.captureGroup)) photo.saved = true;
    for (let index = 0; index < capturePhotos.length; index++) {
      const photo = capturePhotos[index];
      if (photo.saved) continue;
      const progress = `Uploading photo ${index + 1} of ${capturePhotos.length}...`;
      $("captureProgress").textContent = progress; status(progress);
      const body = new FormData();
      body.set("image", photo.file); body.set("viewpoint", photo.viewpoint);
      body.set("photographer", draft.observer); body.set("rights", draft.rights);
      body.set("source_id", draft.object.source_ids.at(-1)); body.set("capture_group", draft.captureGroup); body.set("reviewed", "true");
      await api(`${path}/photos`, { method: "POST", body });
      photo.saved = true;
    }
    const count = capturePhotos.length, name = draft.object.name;
    const lastType = $("objectForm").elements.type.value;
    resetCapture(); showCaptureStep(1); $("objectForm").elements.type.value = lastType; $("captureObserver").value = observer; $("captureRights").value = rights;
    $("search").value = ""; $("gaps").checked = false;
    showCollection(); await inventory(); await showObject(draft.object.id);
    const message = `${name} saved with ${count} photo${count === 1 ? "" : "s"}. Ready to add the next object.`;
    $("captureProgress").textContent = message; status(message);
  });
});

$("finishLater").addEventListener("click", () => {
  if (!captureDraft) return;
  const draft = captureDraft;
  action(async () => {
    const observer = draft.observer;
    resetCapture(); $("captureObserver").value = observer;
    showCollection(); await inventory(); await showObject(draft.object.id);
    status("Object and uploaded photos kept. You can add more photos from its details later.");
  });
});
