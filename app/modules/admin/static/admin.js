"use strict";
const $ = id => document.getElementById(id);
let token = "", locationId = "", objectId = "", offset = 0, venueOffset = 0, venueCount = 0, rows = [], generation = 0;
let previewUrl = "", photoUrls = [], busy = false;
let capturePhotos = [], captureDraft = null, captureReading = false, captureContext = 0, pendingPicker = null;
let guidedCapture = null, guidedLaunching = false;
let pendingDelete = null;
let listPhotoUrls = [];
function clearListImages() {
  listPhotoUrls.forEach(url => URL.revokeObjectURL(url)); listPhotoUrls = [];
}
function confirmObjectDelete(id, name) {
  if (busy) return;
  pendingDelete = { id, path: `/venues/${locationId}/objects/${id}`, name };
  $("deleteObjectName").textContent = name;
  $("deleteError").textContent = "";
  $("deleteDialog").showModal();
}
const tokenStorageKey = "visitadvisor.adminToken";
function storedToken() {
  try { return sessionStorage.getItem(tokenStorageKey) || ""; } catch { return ""; }
}
function rememberToken(value) {
  try {
    if (value) sessionStorage.setItem(tokenStorageKey, value);
    else sessionStorage.removeItem(tokenStorageKey);
  } catch { /* Login still works when browser storage is unavailable. */ }
}
function status(message, isError = false) {
  const feedback = $("status");
  feedback.textContent = message;
  feedback.classList.toggle("srOnly", !isError);
  if (isError && !$("addObject").hidden) {
    $("captureProgress").textContent = message;
    feedback.classList.add("srOnly");
  }
}
function lock() {
  rememberToken("");
  token = ""; generation++; objectId = ""; locationId = "";
  resetCapture(); showCollection(); $("venueDialog").close();
  $("deleteDialog").close();
  clearImages(); clearListImages(); $("detail").replaceChildren(); $("objects").replaceChildren();
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
    const error = new Error(data.error?.message || "Request failed. Please retry.");
    error.code = data.error?.code; error.status = response.status;
    throw error;
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
  document.querySelectorAll("#objectForm input, #objectForm select, #objectForm textarea, #captureIdentity input, #captureIdentity select, #venue, #venueForm button").forEach(el => { el.disabled = busy || captureReading || !!captureDraft; });
  $("createObject").disabled = busy || captureReading || !locationId || !capturePhotos.length;
  $("takePhoto").disabled = $("choosePhotos").disabled = busy || captureReading || !!captureDraft || !locationId || capturePhotos.length >= 4;
  $("guidedCapture").disabled = $("takePhoto").disabled;
  $("cameraInput").disabled = $("libraryInput").disabled = $("takePhoto").disabled;
  $("createObject").textContent = captureDraft ? "Retry save" : "Save object";
  $("captureBack").disabled = busy || !!captureDraft;
  $("openCapture").disabled = busy || captureReading;
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
  clearListImages();
  $("objects").replaceChildren();
  for (const row of visible) {
    const item = document.createElement("div"); item.className = "objectRow";
    const button = document.createElement("button"); button.className = "objectCard";
    button.setAttribute("aria-pressed", String(row.id === objectId));
    const content = document.createElement("span"), name = document.createElement("strong"), description = document.createElement("small"), badge = document.createElement("span");
    name.textContent = row.name;
    description.textContent = `${row.type} · ${row.prepared} ready · ${row.pending} awaiting save`;
    badge.className = `badge${row.needs_photos ? "" : " complete"}`;
    badge.textContent = row.missing ? `${row.missing} more to target ${row.target}` : `Target ${row.target} collected`;
    const thumbnail = document.createElement("span"); thumbnail.className = "objectThumbnail";
    thumbnail.textContent = "No photo";
    if (row.first_photo_id) {
      thumbnail.textContent = "Loading?";
      const venue = locationId;
      fetch(`/api/v1/admin/venues/${venue}/objects/${row.id}/photos/${row.first_photo_id}`, {
        headers: { Authorization: `Bearer ${token}` }, cache: "no-store"
      }).then(async response => {
        if (!response.ok) throw new Error("Photo unavailable");
        const blob = await response.blob();
        if (!item.isConnected) return;
        const url = URL.createObjectURL(blob); listPhotoUrls.push(url);
        const img = document.createElement("img"); img.alt = ""; img.src = url;
        thumbnail.replaceChildren(img);
      }).catch(() => { if (item.isConnected) thumbnail.textContent = "No preview"; });
    }
    content.className = "objectSummary";
    content.append(name, description, badge); button.append(thumbnail, content);
    const remove = document.createElement("button"); remove.type = "button";
    remove.className = "objectDelete"; remove.textContent = "Delete";
    remove.setAttribute("aria-label", `Delete ${row.name}`);
    remove.addEventListener("click", () => confirmObjectDelete(row.id, row.name));
    button.addEventListener("click", () => { if (!busy) showObject(row.id).catch(error => status(error.message, true)); });
    item.append(button, remove); $("objects").append(item);
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
  $("deleteObject").addEventListener("click", () => confirmObjectDelete(id, data.name));
  for (const photo of data.photos) {
    const card = document.createElement("div"); card.className = "photo";
    const img = document.createElement("img"); img.alt = photo.viewpoint;
    const text = document.createElement("p"); text.textContent = photo.viewpoint;
    const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Remove unpublished upload";
    remove.addEventListener("click", () => action(async () => {
      await api(`${path}/photos/${photo._id}`, { method: "DELETE" });
      await inventory(true); await showObject(id); status("Unpublished photo removed.");
    }));
    card.append(img, text, remove); $("photos").append(card);
    if (photo.foreground_mask) {
      const mask = document.createElement("img"); mask.alt = "Saved binary target mask"; mask.hidden = true;
      const toggle = document.createElement("button"); toggle.type = "button"; toggle.textContent = "Show target mask";
      toggle.addEventListener("click", async () => {
        toggle.disabled = true;
        try {
          if (!mask.src) {
            const response = await fetch(`/api/v1/admin${path}/photos/${photo._id}/mask`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" });
            if (response.status === 401) lock();
            if (!response.ok) throw new Error("Target mask unavailable.");
            const blob = await response.blob();
            if (current !== generation) return;
            const url = URL.createObjectURL(blob); photoUrls.push(url); mask.src = url;
          }
          mask.hidden = !mask.hidden; toggle.textContent = mask.hidden ? "Show target mask" : "Hide target mask";
        } catch (error) { status(error.message, true); }
        finally { toggle.disabled = false; }
      });
      card.append(toggle, mask);
    }
    fetch(`/api/v1/admin${path}/photos/${photo._id}`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" }).then(async response => {
      if (!response.ok) throw new Error("Photo preview unavailable.");
      const blob = await response.blob();
      if (current !== generation) return;
      const url = URL.createObjectURL(blob); photoUrls.push(url); img.src = url;
    }).catch(error => { if (current === generation) status(error.message, true); });
  }
  $("saveCollection").hidden = !data.pending;
  $("saveCollection").disabled = data.stale;
  $("saveCollection").addEventListener("click", () => action(() => savePhotoCollection(path, id)));
  $("image").addEventListener("change", () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    const file = $("image").files[0]; $("preview").hidden = !file;
    if (file) { previewUrl = URL.createObjectURL(file); $("preview").src = previewUrl; }
  });
  $("uploadForm").addEventListener("submit", event => {
    event.preventDefault();
    const body = new FormData(event.target);
    body.set("capture_group", `admin-test-${crypto.randomUUID()}`);
    body.set("rights", "Admin test upload; rights details not recorded.");
    const file = body.get("image");
    if (!file || file.size > 5 * 1024 * 1024) { status("Select a photo up to 5 MiB.", true); return; }
    action(async () => {
      await api(`${path}/photos`, { method: "POST", body });
      await savePhotoCollection(path, id);
    });
  });
}
async function savePhotoCollection(path, id) {
  status("Saving photo collection…");
  try {
    await api(`${path}/save`, { method: "POST" });
  } catch (error) {
    // Uploaded photos survive a failed save. Show the same Save action so a
    // retry publishes them without submitting those image files again.
    if (token) {
      try { await inventory(true); await showObject(id); } catch { /* Preserve the save error. */ }
    }
    throw error;
  }
  await inventory(true); await showObject(id); status("Photos saved.");
}
$("cancelDelete").addEventListener("click", () => $("deleteDialog").close());
$("deleteDialog").addEventListener("cancel", event => { if (busy) event.preventDefault(); });
$("deleteDialog").addEventListener("close", () => { pendingDelete = null; $("deleteError").textContent = ""; });
$("confirmDelete").addEventListener("click", () => {
  const selected = pendingDelete;
  if (!selected || busy) return;
  action(async () => {
    $("deleteError").textContent = "";
    try {
      await api(selected.path, { method: "DELETE" });
    } catch (error) {
      // A lost response can leave the object already deleted. Retrying is safe.
      if (error.status !== 404) {
        if (token && $("deleteDialog").open) { $("deleteError").textContent = error.message; return; }
        throw error;
      }
    }
    $("deleteDialog").close();
    if (captureDraft?.object.id === selected.id) resetCapture();
    objectId = ""; clearImages();
    rows = rows.filter(row => row.id !== selected.id); renderObjects();
    $("detail").textContent = "Object deleted.";
    await inventory();
    if (offset && !rows.length) { offset = Math.max(0, offset - 50); await inventory(); }
    status("Object deleted."); $("search").focus();
  });
});
async function action(work) {
  if (busy) return;
  busy = true;
  const controls = [...document.querySelectorAll("button,input,select,textarea")].map(el => [el, el.disabled]);
  controls.forEach(([el]) => { el.disabled = true; });
  try { await work(); } catch (error) {
    if (captureDraft) captureDraft.error = error.message;
    status(error.message, true);
  }
  finally {
    busy = false;
    controls.forEach(([el, disabled]) => { if (el.isConnected) el.disabled = disabled; });
    $("previous").disabled = offset === 0; $("next").disabled = rows.length < 50;
    $("previousVenues").disabled = !!captureDraft || venueOffset === 0; $("nextVenues").disabled = !!captureDraft || venueCount < 100;
    if (captureDraft) {
      $("captureProgress").textContent = captureDraft.stage === "saving"
        ? `Your uploaded photos are kept. ${captureDraft.error || "The collection could not finish saving."} Tap Retry save.`
        : "Some photos could not be uploaded. Your object and completed uploads are saved. Tap Retry save.";
      $("finishLater").hidden = false;
      document.querySelectorAll("#objectForm input, #objectForm select, #objectForm textarea, #captureIdentity input, #captureIdentity select, #venue, #venueForm button").forEach(el => { el.disabled = true; });
    }
    objectVenue();
  }
}
async function unlock() {
  await venues();
  rememberToken(token);
  $("login").hidden = true; $("workspace").hidden = false; $("logout").hidden = false;
  status("");
}
$("loginForm").addEventListener("submit", event => {
  event.preventDefault(); token = $("token").value.trim(); $("token").value = "";
  action(unlock);
});
$("logout").addEventListener("click", () => { lock(); status("Panel locked."); });
$("venue").addEventListener("change", () => { if (captureDraft) { $("venue").value = locationId; status("Finish uploading this object before changing venues.", true); return; } locationId = $("venue").value; offset = 0; inventory().catch(error => status(error.message, true)); });
$("search").addEventListener("input", renderObjects); $("gaps").addEventListener("change", renderObjects);
$("refresh").addEventListener("click", () => action(() => inventory()));
$("previous").addEventListener("click", () => action(async () => { offset = Math.max(0, offset - 50); await inventory(); }));
$("next").addEventListener("click", () => action(async () => { offset += 50; await inventory(); }));
$("previousVenues").addEventListener("click", () => action(async () => { venueOffset = Math.max(0, venueOffset - 100); await venues(); }));
$("nextVenues").addEventListener("click", () => action(async () => { venueOffset += 100; await venues(); }));

$("venueForm").addEventListener("submit", event => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.target));
  action(async () => {
    const venue = await api("/venues", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
    const option = document.createElement("option");
    option.value = venue.id; option.textContent = venue.name;
    $("venue").append(option); $("venue").value = venue.id;
    locationId = venue.id; offset = 0;
    $("search").value = "";
    await inventory();
    $("venueForm").reset();
    $("venueDialog").close(); setTimeout(openCapture, 0);
    status("Venue created. Add its first object.");
  });
});

function restoreCaptureIdentity() { $("captureIdentityHome").append($("captureIdentity")); }
function showCollection() {
  restoreCaptureIdentity();
  captureContext++; pendingPicker = null;
  guidedCapture?.close();
  $("addObject").hidden = true; document.body.classList.remove("wizardOpen"); $("collection").hidden = false; $("collection").inert = false; $("siteHeader").inert = false;
  if (token && !busy) $("openCapture").focus({ preventScroll: true });
}
function openCapture() {
  if (!token || busy || captureReading) return;
  status("");
  if (!locationId) { $("venueDialog").showModal(); return; }
  if (!captureDraft && !capturePhotos.length) { launchGuidedCapture(); return; }
  showCaptureDetails();
}
function showCaptureDetails() {
  restoreCaptureIdentity();
  $("addObject").hidden = false; document.body.classList.add("wizardOpen"); $("collection").hidden = true; $("collection").inert = true; $("siteHeader").inert = true;
  $("stepCount").textContent = "2 of 2";
  $("addObject").scrollTop = 0;
  objectVenue();
  $("detailsTitle").focus({ preventScroll: true });
}
$("openCapture").addEventListener("click", openCapture);
$("closeCapture").addEventListener("click", showCollection);
document.addEventListener("keydown", event => { if (event.key === "Escape" && !busy && !$("guidedDialog").open && !$("addObject").hidden) showCollection(); });
$("captureBack").addEventListener("click", showCollection);
$("openVenue").addEventListener("click", () => $("venueDialog").showModal());
$("closeVenue").addEventListener("click", () => $("venueDialog").close());
function resetCapture() {
  guidedCapture?.close();
  captureContext++; pendingPicker = null;
  $("finishLater").hidden = true;
  capturePhotos.forEach(photo => { URL.revokeObjectURL(photo.url); if (photo.maskUrl) URL.revokeObjectURL(photo.maskUrl); });
  capturePhotos = []; captureDraft = null;
  $("objectForm").reset();
  document.querySelectorAll("#objectForm input, #objectForm select, #objectForm textarea, #captureIdentity input, #captureIdentity select, #venue, #venueForm button").forEach(el => { el.disabled = busy; });
  $("captureProgress").textContent = "";
  renderCapture();
}
function renderCapture() {
  $("capturePhotos").classList.toggle("single", capturePhotos.length === 1);
  $("capturePhotos").replaceChildren();
  capturePhotos.forEach((photo, index) => {
    const quick = photo.captureMetadata?.version === "guided-capture-v3";
    const card = document.createElement("div"); card.className = "capturePhoto";
    const image = document.createElement("img"); image.src = photo.url; image.alt = `Selected photo ${index + 1}`;
    const label = document.createElement("label"); label.textContent = "";
    const select = document.createElement("select"); select.required = !quick; select.setAttribute("aria-label", `Photo ${index + 1} angle`);
    for (const [value, text] of [[quick ? "unknown" : "", quick ? "Angle not confirmed" : "Choose angle"], ["front", "Front"], ["left", "Left side"], ["right", "Right side"], ["back", "Back"], ["detail", "Close-up"], ["other", "Other view"]]) {
      const option = document.createElement("option"); option.value = value; option.textContent = text; select.append(option);
    }
    select.value = photo.viewpoint;
    select.disabled = busy || photo.saved;
    select.addEventListener("change", () => { photo.viewpoint = select.value; if (photo.captureMetadata) photo.captureMetadata.viewpoint_verified = !!select.value && select.value !== "unknown"; });
    label.append(select);
    if (quick) {
      label.hidden = !photo.optionsOpen;
      const options = document.createElement("button"); options.type = "button"; options.className = "maskReview";
      options.textContent = "Photo details"; options.setAttribute("aria-expanded", String(!!photo.optionsOpen));
      options.addEventListener("click", () => { photo.optionsOpen = !photo.optionsOpen; label.hidden = !photo.optionsOpen; options.setAttribute("aria-expanded", String(photo.optionsOpen)); });
      card.append(options);
    }
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "removeCapture";
    remove.textContent = photo.saved ? "Saved" : "\u00d7";
    remove.setAttribute("aria-label", photo.saved ? `Photo ${index + 1} uploaded` : `Remove photo ${index + 1}`);
    remove.disabled = busy || !!captureDraft;
    remove.addEventListener("click", () => {
      URL.revokeObjectURL(photo.url); if (photo.maskUrl) URL.revokeObjectURL(photo.maskUrl); capturePhotos = capturePhotos.filter(item => item !== photo); renderCapture();
    });
    card.append(image, label, remove);
    if (photo.captureMetadata) {
      card.classList.add("guidedSource");
      const hint = document.createElement("span"); hint.className = "guidedHint";
      hint.textContent = quick
        ? ({ pending: "Checking photo…", suggested: "Outline suggested", unavailable: "Photo kept", skipped: "Photo kept" }[photo.captureMetadata.detection_status] || "Photo kept")
        : `Suggested: ${photo.captureMetadata.requested_view}. Choose the actual angle.`;
      card.append(hint);
      if (photo.maskUrl) {
        const toggle = document.createElement("button"); toggle.type = "button"; toggle.className = "maskReview"; toggle.textContent = quick ? "Show suggested target" : "Show isolated target";
        toggle.addEventListener("click", () => { const original = image.src === photo.url; image.src = original ? photo.maskUrl : photo.url; toggle.textContent = original ? "Show original photo" : quick ? "Show suggested target" : "Show isolated target"; }); card.append(toggle);
      }
    }
    $("capturePhotos").append(card);
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
  const session = generation, context = captureContext;
  let message = "";
  try {
    for (const file of files) {
      if (capturePhotos.length >= 4) { message = "Up to 4 photos per object. Remove a photo to choose another."; break; }
      $("captureProgress").textContent = "Optimizing photo on your device...";
      try {
        const optimized = await optimizePhoto(file);
        const digest = await crypto.subtle.digest("SHA-256", await optimized.arrayBuffer());
        const sha = [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, "0")).join("");
        if (session !== generation || context !== captureContext || !token) break;
        if (capturePhotos.some(photo => photo.sha === sha)) { message = "That photo is already selected. Try another angle."; continue; }
        capturePhotos.push({ file: optimized, url: URL.createObjectURL(optimized), sha, viewpoint: capturePhotos.length ? "" : "front", saved: false });
      } catch (error) { message = error.message; }
    }
  } finally {
    captureReading = false; $("captureProgress").textContent = message;
    renderCapture();
  }
  return message;
}
function pickCapturePhotos(inputId) {
  if (busy || captureReading || captureDraft || !locationId || capturePhotos.length >= 4) return;
  guidedCapture?.stopAnalysis?.();
  pendingPicker = { inputId, context: captureContext, venueId: locationId, entries: guidedCapture?.selectedPhotos() || [] };
  guidedCapture?.close();
  $("guidedDialog").close();
  $(inputId).click();
}
$("takePhoto").addEventListener("click", () => pickCapturePhotos("cameraInput"));
$("choosePhotos").addEventListener("click", () => pickCapturePhotos("libraryInput"));
async function importGuidedPhotos(entries, isCurrent) {
  const context = generation, captureSession = captureContext, venue = locationId;
  const staged = [];
  for (const entry of entries) {
    const digest = await crypto.subtle.digest("SHA-256", await entry.file.arrayBuffer());
    if (!isCurrent() || context !== generation || captureSession !== captureContext || venue !== locationId || captureDraft || !token) return;
    const sha = [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, "0")).join("");
    staged.push({ entry, sha });
  }
  if (!isCurrent() || context !== generation || captureSession !== captureContext || venue !== locationId || captureDraft || !token) return;
  for (const { entry, sha } of staged) {
    if (capturePhotos.length >= 4 || capturePhotos.some(photo => photo.sha === sha)) continue;
    capturePhotos.push({ file: entry.file, url: URL.createObjectURL(entry.file), maskUrl: entry.foregroundMask ? URL.createObjectURL(entry.isolatedBlob) : null,
      foregroundMask: entry.foregroundMask, captureMetadata: structuredClone(entry.captureMetadata), sha,
      viewpoint: entry.captureMetadata.version === "guided-capture-v3" ? "unknown" : "", saved: false });
  }
  $("captureProgress").textContent = entries.some(entry => entry.captureMetadata.version === "guided-capture-v3")
    ? "Photos added. Enter a name to save." : "Confirm each photo's actual angle before saving.";
  renderCapture();
}
function updateGuidedPhoto(entry) {
  if (busy || captureDraft || captureReading || !token) return;
  const metadata = entry.captureMetadata;
  const photo = capturePhotos.find(item => !item.saved && item.sha === metadata.selection?.source_sha256
    && item.captureMetadata?.session_id === metadata.session_id && item.captureMetadata?.frame_id === metadata.frame_id);
  if (!photo) return;
  if (photo.maskUrl) URL.revokeObjectURL(photo.maskUrl);
  photo.maskUrl = entry.foregroundMask ? URL.createObjectURL(entry.isolatedBlob) : null;
  photo.foregroundMask = entry.foregroundMask;
  photo.captureMetadata = structuredClone(metadata);
  photo.captureMetadata.viewpoint_verified = !!photo.viewpoint && photo.viewpoint !== "unknown";
  renderCapture();
}
async function launchGuidedCapture(message = "", files = null) {
  if (guidedLaunching || busy || captureReading || captureDraft || !locationId || capturePhotos.length >= 4) return;
  guidedLaunching = true;
  const launchContext = ++captureContext, venueId = locationId;
  try {
    if (!guidedCapture) {
      const { GuidedCapture } = await import('./quick-capture.mjs?v=admin-cosmetic-20261115-1');
      guidedCapture = new GuidedCapture({ capacity: () => 4 - capturePhotos.length,
        analyze: (file, prompt, signal) => api(`/capture/analyze?${new URLSearchParams(prompt)}`, {
          method: "POST", headers: { "Content-Type": "image/jpeg" }, body: file, signal
        }),
        onCancel: () => { captureContext++; if (capturePhotos.length || captureDraft) showCaptureDetails(); else showCollection(); },
        onComplete: () => {
          showCaptureDetails();
          if ($("newObjectName").value.trim() && $("newObjectType").value) $("objectForm").requestSubmit();
        },
        onUse: importGuidedPhotos, onUpdate: updateGuidedPhoto });
    }
    if (launchContext !== captureContext || venueId !== locationId || busy || captureReading || captureDraft || !token) return;
    const type = capturePhotos.length ? $("newObjectType").value : "";
    $("quickDetails").append($("captureIdentity"));
    guidedCapture.open(["painting", "drawing", "print", "photograph"].includes(type) ? "planar" : ["building", "room"].includes(type) ? "scene" : "multiview", { camera: !files });
    if (files) await guidedCapture.importPhotos(files);
    $("guidedUploadStatus").textContent = message;
  } catch {
    if (launchContext !== captureContext || venueId !== locationId || !token) return;
    $("guidedDialog").showModal();
    $("guidedStart").disabled = true;
    $("guidedUploadStatus").textContent = [message, "Guided camera couldn't load. Choose photos or use your phone's camera."].filter(Boolean).join(" ");
  }
  finally { guidedLaunching = false; }
}
$("guidedCapture").addEventListener("click", () => launchGuidedCapture());
// Keep cancellation available even if the guided module itself could not load.
$("guidedClose").addEventListener("click", () => { if (!guidedCapture) { $("guidedDialog").close(); if (!capturePhotos.length) showCollection(); } });
for (const id of ["cameraInput", "libraryInput"]) {
  $(id).addEventListener("change", async event => {
    const files = [...event.target.files]; event.target.value = "";
    const picker = pendingPicker; pendingPicker = null;
    if (!files.length || !token || (picker && (picker.inputId !== id || picker.context !== captureContext || picker.venueId !== locationId))) return;
    const context = captureContext, venue = locationId;
    if (picker?.entries.length) await importGuidedPhotos(picker.entries, () => context === captureContext && venue === locationId && !!token);
    if (context !== captureContext || venue !== locationId || !token) return;
    if (guidedCapture) {
      const optimized = [];
      captureReading = true;
      try {
        for (const file of files.slice(0, 4 - capturePhotos.length)) optimized.push(await optimizePhoto(file));
      } catch (error) { status(error.message); }
      finally { captureReading = false; }
      if (context !== captureContext || venue !== locationId || !token) return;
      if (optimized.length) { await launchGuidedCapture("", optimized); return; }
    }
    guidedCapture?.close(); $("guidedDialog").close();
    status("Optimizing photos on your device...");
    const message = await addCapturePhotos(files);
    if (context !== captureContext || venue !== locationId || !token) return;
    status(message || "");
    if (capturePhotos.length) showCaptureDetails();
    else launchGuidedCapture(message);
  });
  $(id).addEventListener("cancel", async () => {
    const picker = pendingPicker; pendingPicker = null;
    if (!picker || picker.inputId !== id || picker.context !== captureContext || picker.venueId !== locationId || !token) return;
    if (picker.entries.length) await importGuidedPhotos(picker.entries, () => picker.context === captureContext && picker.venueId === locationId && !!token);
    if (picker.context !== captureContext || picker.venueId !== locationId || !token) return;
    if (capturePhotos.length) showCaptureDetails(); else launchGuidedCapture();
  });
}
$("objectForm").addEventListener("submit", event => {
  event.preventDefault();
  if (!captureDraft && !event.target.reportValidity()) return;
  if (!locationId || !capturePhotos.length || captureReading) return;
  guidedCapture?.stopAnalysis?.();
  for (const photo of capturePhotos) {
    if (photo.captureMetadata?.detection_status === "pending") photo.captureMetadata.detection_status = "skipped";
  }
  const fields = Object.fromEntries([...new FormData(event.target)].map(([key, value]) => [key, value.trim()]).filter(([, value]) => value));
  const venueId = captureDraft?.venueId || locationId;
  action(async () => {
    if (!captureDraft) {
      $("captureProgress").textContent = "Saving object...";
      const object = await api(`/venues/${venueId}/objects`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(fields) });
      captureDraft = { venueId, object, captureGroup: `admin-${crypto.randomUUID()}` };
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
      body.set("rights", "Photo rights details not supplied.");
      body.set("source_id", draft.object.source_ids.at(-1) || ""); body.set("capture_group", draft.captureGroup);
      if (photo.captureMetadata) body.set("capture_metadata", JSON.stringify(photo.captureMetadata));
      if (photo.foregroundMask) body.set("foreground_mask", photo.foregroundMask, "foreground-mask.png");
      await api(`${path}/photos`, { method: "POST", body });
      photo.saved = true;
    }
    draft.stage = "saving";
    $("captureProgress").textContent = "Saving photo collection…";
    await api(`${path}/save`, { method: "POST" });
    const count = capturePhotos.length, name = draft.object.name;
    const lastType = $("objectForm").elements.type.value;
    resetCapture(); $("objectForm").elements.type.value = lastType;
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
    resetCapture();
    showCollection(); await inventory(); await showObject(draft.object.id);
    status("Object and uploaded photos kept. You can add more photos from its details later.");
  });
});

// Revalidate the tab's saved token before showing the collection after a reload.
token = storedToken();
if (token) action(unlock);
