// Prepared content is fetched only after a stable tentative camera result.
const panel = document.getElementById('experience');
let active;
let revision = 0;

export function clearExperience() {
  revision++;
  active?.abort();
  active = null;
  panel.replaceChildren();
  panel.hidden = true;
}

function message(text) {
  const p = document.createElement('p');
  p.textContent = text;
  panel.replaceChildren(p);
}

function validateExperience(data, object, catalog) {
  const sources = new Set(object.sources.map(source => source.id));
  if (data.object_id !== object.id || data.location_id !== catalog.location.id ||
      data.reference_version !== catalog.reference_version || data.review_status !== 'unreviewed' ||
      !Array.isArray(data.source_ids) || !data.source_ids.length ||
      !data.source_ids.every(id => sources.has(id)) || !data.content ||
      !Array.isArray(data.content.facts) || data.content.facts.length > 5) throw new Error('Invalid content');
  const content = data.content;
  const sections = [content.introduction, ...content.facts, content.story, content.history, content.interesting_fact];
  for (const section of sections) {
    if (section === null) continue;
    if (!section || typeof section.text !== 'string' || !section.text.trim() ||
        !Array.isArray(section.citations) || !section.citations.length ||
        !section.citations.every(citation => data.source_ids.includes(citation.source_id) &&
          typeof citation.quote === 'string' && citation.quote.trim())) throw new Error('Invalid citation');
  }
  if (content.facts.some(fact => fact === null)) throw new Error('Invalid fact');
  return content;
}

function supportedText(section, object) {
  const block = document.createElement('div');
  const text = document.createElement('p');
  text.textContent = section.text;
  block.append(text);
  const citations = document.createElement('div');
  citations.className = 'content-citations';
  for (const citation of section.citations) {
    const source = object.sources.find(item => item.id === citation.source_id);
    const evidence = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = source.title || 'Source';
    const quote = document.createElement('blockquote');
    quote.textContent = citation.quote;
    evidence.append(summary, quote);
    try {
      const url = new URL(source.url);
      if (url.protocol === 'https:' && !url.username && !url.password) {
        const link = document.createElement('a');
        link.href = url.href;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = 'Open source';
        evidence.append(link);
      }
    } catch { /* Keep the citation even when no public link is available. */ }
    citations.append(evidence);
  }
  block.append(citations);
  return block;
}

function render(content, object) {
  const fragment = document.createDocumentFragment();
  const review = document.createElement('p');
  review.className = 'content-review';
  review.textContent = 'AI-prepared content · Not yet fact-checked';
  fragment.append(review);
  for (const [title, section] of [
    ['Introduction', content.introduction], ['Facts', content.facts],
    ['Story', content.story], ['History', content.history], ['Interesting fact', content.interesting_fact],
  ]) {
    const heading = document.createElement('h3');
    heading.textContent = title;
    fragment.append(heading);
    if (Array.isArray(section) && section.length) {
      const list = document.createElement('ul');
      for (const fact of section) {
        const item = document.createElement('li');
        item.append(supportedText(fact, object));
        list.append(item);
      }
      fragment.append(list);
    } else if (section && !Array.isArray(section)) {
      fragment.append(supportedText(section, object));
    } else {
      const missing = document.createElement('p');
      missing.className = 'content-missing';
      missing.textContent = 'Not available for this object.';
      fragment.append(missing);
    }
  }
  panel.replaceChildren(fragment);
}

export async function showExperience(object, catalog) {
  clearExperience();
  const token = revision;
  const controller = new AbortController();
  active = controller;
  panel.hidden = false;
  message('Loading object information…');
  const timeout = setTimeout(() => controller.abort(), 10000);
  const query = new URLSearchParams({ location_id: catalog.location.id, reference_version: catalog.reference_version });
  try {
    const response = await fetch(`./objects/${encodeURIComponent(object.id)}/experience?${query}`, {
      signal: controller.signal, cache: 'no-store',
    });
    if (token !== revision) return;
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      if (token !== revision) return;
      if (error.error === 'CONTENT_NOT_PREPARED') message('Detailed information has not been prepared for this object.');
      else if (error.error === 'CONTENT_OUTDATED') message('Detailed information needs a source update.');
      else message('Detailed information is unavailable.');
      return;
    }
    const data = await response.json();
    if (token !== revision) return;
    render(validateExperience(data, object, catalog), object);
  } catch {
    if (token === revision) message('Detailed information could not load.');
  } finally {
    clearTimeout(timeout);
    if (active === controller) active = null;
  }
}
