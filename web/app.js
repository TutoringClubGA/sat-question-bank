"use strict";

const byId = (id) => document.getElementById(id);
const picker = byId("picker");
const pickerBody = byId("picker-body");
const sourcePicker = byId("source-picker");
const difficulty = {E: "Easy", M: "Medium", H: "Hard"};
const mathNS = "http://www.w3.org/1998/Math/MathML";
const htmlNS = "http://www.w3.org/1999/xhtml";
const selected = new Map();
const questions = new Map();
const staticSite = Boolean(document.querySelector?.('meta[name="sat-bank-static"]'));

const directory = [
  {id: "reading-writing", name: "Reading & Writing", rows: [
    [
      ["Craft & Structure", [["CTC", "Cross-Text Connections"], ["TSP", "Text Structure & Purpose"], ["WIC", "Words in Context"]]],
      ["Expression of Ideas", [["SYN", "Rhetorical Synthesis"], ["TRA", "Transitions"]]],
    ],
    [
      ["Information & Ideas", [["CID", "Central Ideas & Details"], ["COE", "Command of Evidence"], ["INF", "Inferences"]]],
      ["Standard English Conventions", [["BOU", "Boundaries"], ["FSS", "Form, Structure, & Sense"]]],
    ],
  ]},
  {id: "math", name: "Math", domains: [
    ["Algebra", [
      [["H.A.", "Linear Equations in One Variable"], ["H.C.", "Linear Equations in Two Variables"], ["H.B.", "Linear Functions"]],
      [["H.E.", "Linear Inequalities"], ["H.D.", "Systems of Linear Equations"]],
    ]],
    ["Problem-Solving & Data Analysis", [
      [["Q.G.", "Evaluating Statistical Claims"], ["Q.C.", "One-Variable Data"], ["Q.B.", "Percentages"]],
      [["Q.E.", "Probability"], ["Q.A.", "Ratios, Proportions, Rates, & Units"], ["Q.F.", "Sample Statistics"]],
      [["Q.D.", "Two-Variable Data"]],
    ]],
    ["Geometry & Trigonometry", [
      [["S.A.", "Area & Volume"], ["S.D.", "Circles"]],
      [["S.B.", "Lines, Angles, & Triangles"], ["S.C.", "Right Triangles & Trigonometry"]],
    ]],
    ["Advanced Math", [
      [["P.A.", "Equivalent Expressions"], ["P.B.", "Nonlinear Equations & Systems of Equations"]],
      [["P.C.", "Nonlinear Functions"]],
    ]],
  ]},
];

const dateTargets = [];
const directoryLinks = [];
const directoryCategories = new Map();
let active;
let releaseFilter;
let releaseRowsRequest;
let statisticsLoaded = false;
const monthPicker = byId("month-picker");
const rangeTriggers = [byId("date-from"), byId("date-through")];
let monthTrigger;
let monthYear;
const monthFormat = new Intl.DateTimeFormat("en", {month: "long", year: "numeric", timeZone: "UTC"});
const monthName = new Intl.DateTimeFormat("en", {month: "long", timeZone: "UTC"});
const shortMonthFormat = new Intl.DateTimeFormat("en", {month: "short", timeZone: "UTC"});
const dayFormat = new Intl.DateTimeFormat("en", {dateStyle: "medium", timeZone: "UTC"});
const timeFormat = new Intl.DateTimeFormat("en", {dateStyle: "medium", timeStyle: "medium", timeZone: "UTC"});
const latestRelease = "2026-07-30";
const defaultReleases = ["john", "2026-08"];
const olderLabels = new Set(["Pre-July 2026", "Pre-July 2026 (catalog date)"]);

function day(iso) {
  return iso ? dayFormat.format(new Date(iso + "T00:00:00Z")) : "Unknown";
}

function monthLabel(iso) {
  return monthFormat.format(new Date(iso + "-01T00:00:00Z"));
}

function trackDate(node, codes, level) {
  dateTargets.push({node, codes, level});
  return node;
}

async function populateDirectoryDates() {
  const {dates, database_status: status} = await get("/api/directory-dates");
  setDatabaseDate("database-updated", status.last_update);
  setDatabaseDate("database-changed", status.last_change);
  if (status.change_from_catalog) {
    byId("database-changed").title = "Latest dated College Board change in this snapshot";
  }
  byId("database-dates").hidden = false;
  for (const {node, codes, level} of dateTargets) {
    const latest = dates.filter((row) => codes.includes(row.skill_code) && (!level || row.difficulty === level))
      .reduce((max, row) => row.latest > max ? row.latest : max, "");
    node.title = latest ? `Most recent question · ${day(latest)}` : "No questions";
  }
}

function setDatabaseDate(id, timestamp) {
  const time = byId(id);
  time.textContent = timestamp ? dayFormat.format(new Date(timestamp)) : "Unknown";
  if (timestamp) time.dateTime = timestamp;
}

function element(tag, content, className) {
  const node = document.createElement(tag);
  node.textContent = content ?? "";
  if (className) node.className = className;
  return node;
}

async function get(path) {
  const url = staticSite ? `./data/${path.slice("/api/".length)}.json` : path;
  const response = await fetch(url);
  if (staticSite && !response.ok) throw new Error(`Question bank request failed (${response.status}).`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error);
  return data;
}

function showError(error) {
  byId("error").textContent = error?.message ?? "";
  byId("error").hidden = !error;
}

function categoryKey(section, code, level) {
  return JSON.stringify([section, code, level]);
}

function difficultyLink(label, category, answers) {
  const button = element("button", label, answers ? "text-link answer-link" : "text-link");
  button.type = "button";
  button.setAttribute("aria-haspopup", "dialog");
  button.setAttribute("aria-controls", "picker");
  button.setAttribute("aria-expanded", "false");
  const accessibleName = [category.skill, difficulty[category.level], answers ? "Answer Key" : ""].filter(Boolean).join(", ");
  button.setAttribute("aria-label", accessibleName);
  const key = categoryKey(category.section, category.code, category.level);
  directoryLinks.push({button, label, accessibleName, key, answers});
  button.addEventListener("click", () => {
    const filteredIds = releaseFilter?.groups.get(key);
    if (releaseFilter && !filteredIds?.length) return;
    if (answers && filteredIds) {
      printSelection(selectionFor(category, filteredIds), true, button);
    } else if (active?.anchor === button && picker.matches(":popover-open")) {
      closePicker();
    } else {
      openPicker(button, category, answers, filteredIds);
    }
  });
  return trackDate(button, [category.code], category.level);
}

function buildSkill(section, domain, [code, skill]) {
  const block = element("div", "", "skill");
  const list = element("ul", "", "difficulty-list");
  block.append(trackDate(element("h4", skill), [code]), list);
  for (const [level, label] of Object.entries(difficulty)) {
    const category = {section, domain, skill, code, level};
    directoryCategories.set(categoryKey(section, code, level), category);
    const entry = element("li");
    const answer = element("div", "", "answer-row");
    answer.append(difficultyLink("Answer Key", category, true));
    entry.append(difficultyLink(label, category, false), answer);
    list.append(entry);
  }
  return block;
}

function buildDomain(section, name, columns) {
  const block = element("section", "", "domain");
  const grid = element("div", "", "skill-columns");
  grid.style.setProperty("--columns", columns.length);
  if (columns.length === 3) grid.classList.add("three-column-domain");
  for (const skills of columns) {
    const column = element("div", "", "skill-column");
    column.append(...skills.map((skill) => buildSkill(section, name, skill)));
    grid.append(column);
  }
  block.append(trackDate(element("h3", name), columns.flat().map(([code]) => code)), grid);
  return block;
}

function buildDirectory() {
  for (const section of directory) {
    const block = element("section", "", "bank-section");
    block.id = section.id;
    const codes = section.rows
      ? section.rows.flat().flatMap(([, skills]) => skills)
      : section.domains.flatMap(([, columns]) => columns.flat());
    block.append(trackDate(element("h2", section.name), codes.map(([code]) => code)));
    for (const row of section.rows ?? []) {
      const group = element("div", "", "domain-pair");
      group.append(...row.map(([name, skills]) => buildDomain(section.name, name, [skills])));
      block.append(group);
    }
    for (const [name, columns] of section.domains ?? []) {
      block.append(buildDomain(section.name, name, columns));
    }
    byId("directory").append(block);
  }
}

const sectionButtons = document.querySelectorAll(".section-switcher button");
function showSection(id) {
  for (const button of sectionButtons) {
    const sectionId = button.getAttribute("aria-controls");
    const current = sectionId === id;
    button.setAttribute("aria-pressed", String(current));
    byId(sectionId).hidden = !current;
  }
  const showingStatistics = id === "statistics";
  byId("directory-header").hidden = showingStatistics;
  if (showingStatistics && !statisticsLoaded) {
    const status = byId("statistics-status");
    status.textContent = "Loading question history…";
    loadReleaseRows().then(renderStatistics).catch((error) => {
      status.textContent = error.message;
    });
  }
}

function buildSources(rows) {
  const sources = new Map();
  function add(key, label, questionId) {
    if (!sources.has(key)) sources.set(key, {label, ids: []});
    sources.get(key).ids.push(questionId);
  }
  for (const row of rows) {
    const {question_id: id, release_period: period, catalog_date: date} = row;
    if (period === "Mr. Thomas release") {
      add("thomas", "Mr. Thomas' bank", id);
    } else if (period === "July 30, 2026") {
      add("john", "July 30 Release", id);
    } else if (olderLabels.has(period) || (date && date < latestRelease)) {
      add("older", "Pre-Mr. Thomas", id);
    }
    if (date) {
      const month = date.slice(0, 7);
      add(month, monthLabel(month), id);
    }
  }
  return sources;
}

function renderSources(state) {
  const keys = [...state.sources.keys()];
  const months = keys.filter((key) => /^\d{4}-/.test(key)).sort();
  const ordered = ["older", "thomas", "john", ...months];
  for (const [container, sourceKeys] of [
    [byId("source"), ordered.filter((key) => !months.includes(key) || key >= "2026-08")],
    [byId("earlier-sources"), months.filter((key) => key < "2026-08")],
  ]) {
    container.replaceChildren(...sourceKeys.flatMap((key) => {
      const source = state.sources.get(key);
      if (!source) return [];
      const label = element("label", "", "source-option");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = key;
      checkbox.checked = state.chosen.has(key);
      label.append(checkbox, element("span", source.label), element("span", source.ids.length.toLocaleString(), "source-count"));
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) state.chosen.add(key);
        else state.chosen.delete(key);
        rebuildSelection(state);
      });
      return [label];
    }));
  }
  byId("earlier-months").hidden = !byId("earlier-sources").childElementCount;
}

function releaseIds(state) {
  const ids = new Set();
  for (const key of state.chosen) {
    for (const id of state.sources.get(key).ids) ids.add(id);
  }
  for (const id of state.range?.ids ?? []) ids.add(id);
  return ids;
}

function loadReleaseRows() {
  if (!releaseRowsRequest) {
    releaseRowsRequest = get("/api/directory-releases").then(({questions: rows}) => {
      const sources = buildSources(rows);
      const chosen = new Set(defaultReleases.filter((key) => sources.has(key)));
      const state = {rows, sources, chosen};
      updateReleaseFilter(state, releaseIds(state));
      return rows;
    }).catch((error) => {
      releaseRowsRequest = undefined;
      throw error;
    });
  }
  return releaseRowsRequest;
}

function rebuildSelection(state) {
  const ids = releaseIds(state);
  state.ids = state.rows.map((row) => row.question_id).filter((id) => ids.has(id));
  if (!state.category && state.ready) updateReleaseFilter(state, ids);
  updatePicker(state);
}

function questionCount(count) {
  return `${count.toLocaleString()} ${count === 1 ? "question" : "questions"}`;
}

function renderStatistics(rows) {
  if (statisticsLoaded) return;
  const dates = new Map();
  for (const row of rows) {
    const date = row.added_date ?? row.catalog_date ?? "";
    if (!dates.has(date)) dates.set(date, []);
    dates.get(date).push(row);
  }
  const tableBody = byId("statistics-rows");
  for (const date of [...dates.keys()].sort().reverse()) {
    const additions = dates.get(date);
    const mathCount = additions.filter(({section}) => section === "Math").length;
    const row = element("tr");
    const dateCell = element("th");
    dateCell.scope = "row";
    const toggle = element("button", day(date), "text-link statistics-date");
    toggle.type = "button";
    const detail = element("tr", "", "statistics-detail");
    detail.id = "additions-" + (date || "unknown");
    detail.hidden = true;
    const content = element("td");
    content.colSpan = 4;
    detail.append(content);
    toggle.setAttribute("aria-controls", detail.id);
    toggle.setAttribute("aria-expanded", "false");
    toggle.addEventListener("click", () => {
      detail.hidden = !detail.hidden;
      toggle.setAttribute("aria-expanded", String(!detail.hidden));
      if (!detail.hidden && !content.childElementCount) renderAdditions(content, additions);
    });
    dateCell.append(toggle);
    row.append(dateCell, ...[additions.length - mathCount, mathCount, additions.length]
      .map((count) => element("td", count.toLocaleString())));
    tableBody.append(row, detail);
  }
  byId("statistics-table").hidden = !dates.size;
  const status = byId("statistics-status");
  status.hidden = Boolean(dates.size);
  status.textContent = dates.size ? "" : "No questions available.";
  statisticsLoaded = true;
}

function renderAdditions(content, additions) {
  for (const {name: section} of directory) {
    const groups = new Map();
    for (const row of additions) {
      if (row.section !== section) continue;
      const key = categoryKey(section, row.skill_code, row.difficulty);
      if (!groups.has(key)) {
        const category = directoryCategories.get(key) ?? {skill: row.skill, level: row.difficulty};
        groups.set(key, {category, ids: []});
      }
      groups.get(key).ids.push(row.question_id);
    }
    if (!groups.size) continue;
    const list = element("ul");
    const ordered = [...groups.values()].sort((a, b) => a.category.skill.localeCompare(b.category.skill)
      || "EMH".indexOf(a.category.level) - "EMH".indexOf(b.category.level));
    for (const {category, ids} of ordered) {
      const item = element("li");
      const label = `${category.skill} · ${difficulty[category.level] ?? category.level} (${questionCount(ids.length)})`;
      const button = element("button", label, "text-link");
      button.type = "button";
      button.addEventListener("click", () => printSelection(selectionFor(category, ids), false, button));
      item.append(button);
      list.append(item);
    }
    content.append(element("h3", section), list);
  }
}

function updateReleaseFilter(state, ids) {
  const {chosen, range} = state;
  if (!chosen.size && !range) {
    releaseFilter = undefined;
  } else {
    const groups = new Map();
    for (const row of state.rows) {
      if (!ids.has(row.question_id)) continue;
      const key = categoryKey(row.section, row.skill_code, row.difficulty);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(row.question_id);
    }
    releaseFilter = {chosen, range, groups, summary: releaseMonthSummary(state)};
  }
  renderReleaseFilter();
}

function releaseMonthSummary({chosen, sources, range, rows}) {
  const months = new Set([...chosen].filter((key) => /^\d{4}-\d{2}$/.test(key)));
  if (chosen.has("john")) months.add(latestRelease.slice(0, 7));
  const historicalIds = new Set(["older", "thomas"].filter((key) => chosen.has(key))
    .flatMap((key) => sources.get(key).ids));
  for (const row of rows) {
    if (row.catalog_date && historicalIds.has(row.question_id)) months.add(row.catalog_date.slice(0, 7));
  }
  const spans = [...months].map((month) => [month, month]);
  if (range) spans.push([range.from, range.through]);
  const merged = [];
  function monthIndex(month) {
    const [year, number] = month.split("-").map(Number);
    return year * 12 + number;
  }
  for (const [start, end] of spans.sort(([a], [b]) => a.localeCompare(b))) {
    const previous = merged.at(-1);
    if (!previous || monthIndex(start) > monthIndex(previous[1]) + 1) merged.push([start, end]);
    else if (end > previous[1]) previous[1] = end;
  }
  const labels = merged.map(([start, end]) => {
    if (start === end) return monthLabel(start);
    const sameYear = start.slice(0, 4) === end.slice(0, 4);
    const first = sameYear ? monthName.format(new Date(start + "-01T00:00:00Z")) : monthLabel(start);
    return `${first} – ${monthLabel(end)}`;
  });
  return labels.join(", ");
}

function renderReleaseFilter() {
  const filtered = Boolean(releaseFilter);
  for (const {button, label, accessibleName, key, answers} of directoryLinks) {
    const count = releaseFilter?.groups.get(key)?.length ?? 0;
    button.disabled = filtered && !count;
    button.closest("li").classList.toggle("no-matches", button.disabled);
    button.textContent = filtered && !answers ? `${label} (${questionCount(count)})` : label;
    button.setAttribute("aria-label", filtered ? `${accessibleName}, ${questionCount(count)}` : accessibleName);
    for (const [attribute, value] of [
      ["aria-haspopup", "dialog"], ["aria-controls", "picker"], ["aria-expanded", String(active?.anchor === button)],
    ]) {
      if (filtered && answers) button.removeAttribute(attribute);
      else button.setAttribute(attribute, value);
    }
  }
  byId("clear-release").hidden = !filtered;
  const summary = byId("release-summary");
  const months = releaseFilter?.summary;
  summary.textContent = months ? `Showing ${months}` : "No release filter";
  summary.title = months ? summary.textContent : "";
}

function positionPicker() {
  if (!active || !picker.matches(":popover-open")) return;
  const margin = 10;
  const anchor = active.anchor.getBoundingClientRect();
  // Reserve room for disclosures to grow without repositioning the open picker.
  const reservedHeight = Math.min(active.filteredCategory ? 170 : 440, window.innerHeight - 2 * margin);
  const top = Math.max(margin, Math.min(anchor.bottom + 7, window.innerHeight - margin - reservedHeight));
  picker.style.left = Math.max(margin, Math.min(anchor.left, window.innerWidth - picker.offsetWidth - margin)) + "px";
  picker.style.top = top + "px";
  picker.style.maxHeight = window.innerHeight - top - margin + "px";
}

function focusPickerControl(control, group = control) {
  control.focus({preventScroll: true});
  const viewport = pickerBody.getBoundingClientRect();
  const block = group.getBoundingClientRect();
  const bounds = block.height + 8 <= viewport.height ? block : control.getBoundingClientRect();
  if (bounds.bottom > viewport.bottom - 4) {
    pickerBody.scrollTop += bounds.bottom - viewport.bottom + 4;
  } else if (bounds.top < viewport.top + 4) {
    pickerBody.scrollTop += bounds.top - viewport.top - 4;
  }
}

function closePicker(restoreFocus = true) {
  const anchor = active?.anchor;
  closeMonthPicker();
  if (picker.matches(":popover-open")) picker.hidePopover();
  anchor?.setAttribute("aria-expanded", "false");
  active = undefined;
  if (restoreFocus) anchor?.focus({preventScroll: true});
}

async function openPicker(anchor, category, answers, filteredIds) {
  if (preparingPrint) return;
  closeMonthPicker();
  active?.anchor.setAttribute("aria-expanded", "false");
  const filteredCategory = filteredIds !== undefined;
  const state = {
    anchor, category, answers, filteredCategory, rows: [], sources: new Map(), chosen: new Set(),
    ids: filteredIds ?? [], ready: filteredCategory, busy: false,
  };
  const isFilter = !category;
  active = state;
  anchor.setAttribute("aria-expanded", "true");
  byId("picker-title").textContent = category?.skill ?? "";
  byId("picker-title").hidden = isFilter;
  byId("picker-context").textContent = isFilter ? "" : difficulty[category.level] + (answers ? " · Answer Key" : "");
  byId("picker-context").hidden = isFilter;
  picker.classList.toggle("is-release-filter", isFilter);
  picker.classList.toggle("is-filtered-category", filteredCategory);
  picker.setAttribute("aria-labelledby", isFilter ? "filter-release" : "picker-title");
  sourcePicker.hidden = filteredCategory;
  const status = byId("picker-status");
  if (isFilter) sourcePicker.append(status);
  else sourcePicker.after(status);
  byId("add-questions").hidden = isFilter || answers;
  byId("select-questions").hidden = isFilter;
  if (!filteredCategory) {
    byId("earlier-months").open = false;
    byId("date-range").open = false;
    resetRangeMonths();
    byId("clear-range").hidden = true;
    byId("range-status").hidden = true;
    renderSources(state);
  }
  updatePicker(state);
  if (!picker.matches(":popover-open")) picker.showPopover();
  positionPicker();
  pickerBody.scrollTop = 0;
  if (filteredCategory) {
    byId("select-questions").focus({preventScroll: true});
    return;
  }
  if (isFilter) byId("close-picker").focus({preventScroll: true});
  try {
    let rows;
    if (category && !staticSite) {
      const query = new URLSearchParams({section: category.section, skill_code: category.code, difficulty: category.level});
      rows = (await get(`/api/picker?${query}`)).questions;
    } else {
      rows = await loadReleaseRows();
      if (category) rows = rows.filter((row) => row.section === category.section &&
        row.skill_code === category.code && row.difficulty === category.level);
    }
    if (active !== state) return;
    state.rows = rows;
    state.sources = buildSources(rows);
    const saved = releaseFilter;
    if (category) {
      state.chosen = new Set([...state.sources.keys()].filter((key) => key === "john" || /^\d{4}-/.test(key) && key >= "2026-08"));
    } else if (saved) {
      state.chosen = new Set([...saved.chosen].filter((key) => state.sources.has(key)));
      if (saved.range) {
        const {from, through} = saved.range;
        setRangeMonth(rangeTriggers[0], from);
        setRangeMonth(rangeTriggers[1], through);
        byId("date-range").open = true;
        setRange(state, from, through);
      }
      byId("earlier-months").open = [...state.chosen].some((key) => /^\d{4}-/.test(key) && key < "2026-08");
    }
    state.ready = true;
    renderSources(state);
    rebuildSelection(state);
  } catch (error) {
    if (active === state) byId("picker-status").textContent = error.message;
  }
}

function setRangeMonth(trigger, value) {
  trigger.value = value;
  trigger.textContent = value ? monthLabel(value) : "Select month";
}

function resetRangeMonths() {
  for (const trigger of rangeTriggers) setRangeMonth(trigger, "");
}

function closeMonthPicker(restoreFocus = false) {
  if (!monthTrigger) return;
  const trigger = monthTrigger;
  monthTrigger = undefined;
  monthPicker.hidden = true;
  trigger.setAttribute("aria-expanded", "false");
  if (restoreFocus) focusPickerControl(trigger);
}

function renderMonthPicker() {
  byId("month-year").textContent = monthYear;
  const buttons = Array.from({length: 12}, (_, index) => {
    const value = String(monthYear).padStart(4, "0") + "-" + String(index + 1).padStart(2, "0");
    const label = shortMonthFormat.format(new Date(value + "-01T00:00:00Z"));
    const button = element("button", label);
    button.type = "button";
    button.setAttribute("aria-label", monthLabel(value));
    button.setAttribute("aria-pressed", String(value === monthTrigger.value));
    button.addEventListener("click", () => {
      setRangeMonth(monthTrigger, value);
      closeMonthPicker(true);
    });
    return button;
  });
  byId("month-options").replaceChildren(...buttons);
  return buttons.find((button) => button.getAttribute("aria-pressed") === "true") ?? buttons[0];
}

function openMonthPicker(trigger) {
  if (monthTrigger === trigger) {
    closeMonthPicker();
    return;
  }
  closeMonthPicker();
  monthTrigger = trigger;
  monthYear = Number(trigger.value.slice(0, 4)) || 2026;
  trigger.setAttribute("aria-expanded", "true");
  monthPicker.hidden = false;
  focusPickerControl(renderMonthPicker(), monthPicker);
}

function applyRange() {
  if (!active?.ready) return;
  closeMonthPicker();
  const [from, through] = rangeTriggers.map((trigger) => trigger.value);
  const status = byId("range-status");
  status.hidden = false;
  if (!from || !through || from > through) {
    status.textContent = "Choose a start and end month in order.";
    return;
  }
  setRange(active, from, through);
  active.chosen.clear();
  for (const checkbox of sourcePicker.querySelectorAll(".source-option input")) checkbox.checked = false;
  rebuildSelection(active);
}

function setRange(state, from, through) {
  const ids = state.rows.filter(({catalog_date: date}) => {
    const month = date?.slice(0, 7);
    return month >= from && month <= through;
  }).map((row) => row.question_id);
  state.range = {from, through, ids, label: `${monthLabel(from)}–${monthLabel(through)}`};
  const status = byId("range-status");
  status.hidden = false;
  status.textContent = `${state.range.label} · ${questionCount(ids.length)} in range`;
  byId("clear-range").hidden = false;
}

function updatePicker(state) {
  const count = state.ids.length;
  const hasReleaseSelection = state.chosen.size > 0 || Boolean(state.range);
  let emptyStatus = "";
  if (hasReleaseSelection) emptyStatus = "0 questions";
  else if (!state.rows.length) emptyStatus = state.category ? "No questions for this difficulty." : "No questions available.";
  byId("picker-status").textContent = !state.ready ? "Loading questions…" : count ? questionCount(count) : emptyStatus;
  byId("add-questions").disabled = !count || state.busy;
  byId("select-questions").disabled = !count || state.busy;
  sourcePicker.disabled = !state.ready || state.busy;
}

function selectionFor(category, ids) {
  return new Map(ids.map((id) => [id, category]));
}

function questionDetails(question) {
  const container = element("div");
  const metadata = question.catalog_metadata;
  const timestamp = (value) => value && !Number.isNaN(new Date(value).getTime())
    ? timeFormat.format(new Date(value)) + " UTC" : null;
  const release = {
    "July 30, 2026": "July 30 Release",
    "Mr. Thomas release": "Mr. Thomas' bank",
    "Pre-July 2026": "Pre-Mr. Thomas",
    "Pre-July 2026 (catalog date)": "Pre-Mr. Thomas (catalog date)",
  };
  const fields = [
    ["Question ID", question.question_id],
    ["Section", question.section],
    ["Domain", question.domain],
    ["Skill", question.skill],
    ["Difficulty", difficulty[question.difficulty] ?? question.difficulty],
    ["Format", {mcq: "Multiple choice", spr: "Student-produced response"}[question.question_type] ?? question.question_type],
    ["Release evidence", release[question.release_period] ?? question.release_period],
    ["Catalog created", timestamp(metadata.createDate)],
    ["Catalog updated", timestamp(metadata.updateDate)],
    ["First seen", timestamp(question.first_seen_at)],
    ["Last seen", timestamp(question.last_seen_at)],
    ["Availability", question.is_listed ? "Currently listed" : "Removed from catalog"],
    ["External ID", question.external_id],
    ["Internal ID", question.uid],
    ["Paper-test item number", question.ibn],
    ["Domain code", question.domain_code],
    ["Skill code", question.skill_code],
    ["First import run", question.first_seen_run],
    ["Last import run", question.last_seen_run],
  ];
  const list = element("dl");
  for (const [label, value] of fields) {
    if (value == null || value === "") continue;
    list.append(element("dt", label), element("dd", String(value)));
  }
  container.append(list);
  if (question.release_sources.length) {
    const evidence = element("ul", "", "metadata-sources");
    for (const source of question.release_sources) {
      const item = element("li");
      if (source.source.startsWith("https://")) {
        const label = source.match_method.startsWith("mr_thomas:") ? "Mr. Thomas’s question PDF" : source.period + " source";
        const link = element("a", label);
        link.href = source.source;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        item.append(link);
      } else {
        item.textContent = source.source;
      }
      evidence.append(item);
    }
    container.append(evidence);
  }
  const raw = element("details", "", "raw-metadata");
  raw.append(element("summary", "Raw catalog metadata"), element("pre", JSON.stringify(metadata, null, 2)));
  container.append(raw);
  return container;
}

function updateWorksheet() {
  byId("selection-count").textContent = `${selected.size} selected`;
  byId("worksheet-bar").hidden = !selected.size;
  const includedCategories = new Set([...selected.values()].map(({section, code, level}) => categoryKey(section, code, level)));
  for (const {button, key, answers} of directoryLinks) {
    if (answers) continue;
    const included = includedCategories.has(key);
    button.closest("li").classList.toggle("worksheet-included", included);
    if (included) button.setAttribute("aria-describedby", "worksheet-included-description");
    else button.removeAttribute("aria-describedby");
  }
}

function expandFences(root) {
  for (const fence of root.querySelectorAll("mfenced")) {
    const row = document.createElementNS(mathNS, "mrow");
    const separator = (fence.getAttribute("separators") ?? ",").replace(/\s/g, "");
    const operator = (text) => {
      const op = document.createElementNS(mathNS, "mo");
      op.textContent = text;
      row.append(op);
    };
    const open = fence.getAttribute("open") ?? "(";
    const close = fence.getAttribute("close") ?? ")";
    const children = [...fence.children];
    if (open) operator(open);
    children.forEach((child, index) => {
      if (index && separator) operator(separator[Math.min(index - 1, separator.length - 1)]);
      row.append(child);
    });
    if (close) operator(close);
    fence.replaceWith(row);
  }
}

function sizeQuestionDiagram(image) {
  if (image.matches(".math-img, [role=math]") || image.closest("math, .math-container, .math_expression")) return;
  const vector = image.getAttribute("src")?.startsWith("data:image/svg+xml;");
  const heightLimit = image.closest(".choice") ? 185 : 280;
  function applySize() {
    const {naturalWidth, naturalHeight} = image;
    const width = Number(image.getAttribute("width")) || naturalWidth;
    const height = Number(image.getAttribute("height")) || naturalHeight;
    if (width <= 0 || height <= 0) return;
    const ratio = naturalWidth && naturalHeight ? naturalWidth / naturalHeight : width / height;
    const rasterLimit = vector || !naturalWidth ? Infinity : naturalWidth * 1.35;
    const widthLimit = Math.max(width, Math.min(heightLimit * ratio, rasterLimit));
    image.style.setProperty("--diagram-width", `${width}px`);
    image.style.setProperty("--diagram-width-limit", `${widthLimit}px`);
    image.classList.add("question-diagram");
  }
  if (!image.complete || !image.naturalWidth) image.addEventListener("load", applySize, {once: true});
  applySize();
}

function appendMarkup(container, source) {
  if (!source) return;
  const template = document.createElement("template");
  template.innerHTML = source;
  template.content.querySelectorAll("script, iframe, object, embed, link, meta, base, form, input, button, audio, video, foreignObject").forEach((node) => node.remove());
  for (const node of template.content.querySelectorAll("*")) {
    for (const attribute of [...node.attributes]) {
      if (/^on/i.test(attribute.name) || attribute.name === "srcdoc" ||
          (["src", "href", "xlink:href"].includes(attribute.name) &&
           !/^(#|https:\/\/|data:image\/(png|jpeg|gif|webp);)/i.test(attribute.value))) {
        node.removeAttribute(attribute.name);
      }
    }
  }
  // SVG as an image isolates its styles and preserves its original viewBox.
  for (const svg of template.content.querySelectorAll("svg")) {
    const image = document.createElementNS(htmlNS, "img");
    image.alt = svg.getAttribute("aria-label") || svg.querySelector("title")?.textContent || "Question figure";
    image.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(new XMLSerializer().serializeToString(svg));
    svg.replaceWith(image);
  }
  template.content.querySelectorAll("style").forEach((node) => node.remove());
  expandFences(template.content);
  const images = [...template.content.querySelectorAll("img")];
  container.append(template.content);
  images.forEach(sizeQuestionDiagram);
}

function questionContent(question, answerKey = false) {
  const content = element("div", "", "question");
  if (answerKey) {
    if (question.correct_answers.length) {
      const label = question.answer_source === "rationale" ? "Answer from explanation" : "Answer";
      content.append(element("strong", `${label}: ${question.correct_answers.join(", ")}`));
    }
    appendMarkup(content, question.explanation);
    return content;
  }
  appendMarkup(content, question.stimulus);
  appendMarkup(content, question.stem);
  for (const [index, option] of question.answer_options.entries()) {
    const choice = element("div", "", "choice");
    const label = /^[A-Z]$/i.test(option.id) ? option.id.toUpperCase() : String.fromCharCode(65 + index);
    const body = element("div");
    choice.append(element("span", `${label}.`), body);
    appendMarkup(body, option.content);
    content.append(choice);
  }
  if (question.question_type === "spr") content.append(element("span", "", "answer-line"));
  return content;
}

async function getQuestion(id) {
  if (!questions.has(id)) questions.set(id, await get(`/api/question/${encodeURIComponent(id)}`));
  return questions.get(id);
}

function updatePrintTitle() {
  const included = byId("include-title").checked;
  const title = byId("title-input").value.trim();
  byId("title-field").hidden = !included;
  byId("print-title").textContent = title;
  byId("print-title").hidden = !included || !title;
}

function updatePrintCount({selection, excluded}) {
  const count = selection.size - excluded.size;
  byId("print-selection-count").textContent = excluded.size ? `${count} of ${selection.size} selected` : "";
  byId("print").disabled = !count;
}

function worksheetItem(question, number, session, answerKey) {
  const item = element("section", "", "print-item");
  const id = question.question_id;
  const controls = element("div", "", "print-item-controls");
  const label = element("label", "", "print-inclusion");
  const checkbox = element("input");
  checkbox.type = "checkbox";
  checkbox.checked = !session.excluded.has(id);
  checkbox.setAttribute("aria-label", `Include question ${number}`);
  item.classList.toggle("is-excluded", !checkbox.checked);
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) session.excluded.delete(id);
    else session.excluded.add(id);
    item.classList.toggle("is-excluded", !checkbox.checked);
    updatePrintCount(session);
  });
  label.append(checkbox, element("strong", `${number}.`, "preview-number"));

  const details = element("button", "Details", "text-link question-detail-link");
  details.type = "button";
  details.setAttribute("aria-label", `Details for question ${number}`);
  details.setAttribute("aria-haspopup", "dialog");
  details.setAttribute("aria-controls", "question-detail-dialog");
  details.addEventListener("click", () => {
    byId("question-detail-title").textContent = `Question ${number} details`;
    byId("question-detail-content").replaceChildren(questionDetails(question));
    byId("question-detail-dialog").showModal();
    byId("close-question-detail").focus();
  });
  controls.append(label, details);
  item.append(controls, questionContent(question, answerKey));
  return item;
}

async function renderPrint(session, answerKey) {
  const ids = [...session.selection.keys()];
  const content = [];
  for (let index = 0; index < ids.length; index += 12) {
    const batch = await Promise.all(ids.slice(index, index + 12).map(getQuestion));
    for (const question of batch) {
      content.push(worksheetItem(question, content.length + 1, session, answerKey));
    }
  }
  byId("print-questions").replaceChildren(...content);
  byId("switch-print").textContent = answerKey ? "Switch to Questions" : "Switch to answer key";
  updatePrintCount(session);
}

let returnTo;
let worksheetSession;
let printingAnswers;
let preparingPrint = false;
async function printSelection(selection, answers, requestedBy) {
  if (preparingPrint || !selection.size) return;
  preparingPrint = true;
  const origin = active?.anchor ?? requestedBy;
  returnTo = origin;
  const state = active;
  if (state) {
    state.busy = true;
    updatePicker(state);
  }
  const controls = ["worksheet", "clear"].map(byId);
  controls.forEach((button) => { button.disabled = true; });
  showError();
  try {
    byId("include-title").checked = false;
    byId("title-input").value = [...new Set([...selection.values()].map(({skill}) => skill))].join(", ");
    updatePrintTitle();
    const session = {selection, excluded: new Set()};
    await renderPrint(session, answers);
    if (returnTo !== origin) return;
    worksheetSession = session;
    printingAnswers = answers;
    byId("switch-print").hidden = requestedBy.id === "worksheet";
    closePicker(false);
    byId("browser").hidden = true;
    byId("print-view").hidden = false;
    byId("back").textContent = byId("statistics").hidden ? "Back to database" : "Back to database statistics";
    byId("back").focus();
    window.scrollTo(0, 0);
  } catch (error) {
    if (returnTo !== origin) return;
    if (state && active === state) {
      state.busy = false;
      updatePicker(state);
      byId("picker-status").textContent = error.message;
    } else {
      showError(error);
    }
  } finally {
    preparingPrint = false;
    controls.forEach((button) => { button.disabled = false; });
  }
}

picker.setAttribute("role", "dialog");
picker.addEventListener("toggle", () => {
  if (!picker.matches(":popover-open") && active) closePicker(false);
});
picker.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    if (monthTrigger) closeMonthPicker(true);
    else closePicker();
  }
});
picker.addEventListener("click", (event) => {
  if (!monthPicker.contains(event.target) && !event.target.closest(".month-trigger")) closeMonthPicker();
});
byId("close-picker").addEventListener("click", () => closePicker());
byId("filter-release").addEventListener("click", (event) => {
  const anchor = event.currentTarget;
  if (active?.anchor === anchor && picker.matches(":popover-open")) closePicker();
  else openPicker(anchor);
});
byId("clear-release").addEventListener("click", () => {
  closePicker(false);
  releaseFilter = undefined;
  renderReleaseFilter();
  byId("filter-release").focus();
});
window.addEventListener("resize", positionPicker);
for (const trigger of rangeTriggers) {
  trigger.addEventListener("click", () => openMonthPicker(trigger));
}
for (const [id, change] of [["previous-year", -1], ["next-year", 1]]) {
  byId(id).addEventListener("click", () => {
    monthYear += change;
    renderMonthPicker();
  });
}
byId("apply-range").addEventListener("click", applyRange);
byId("clear-range").addEventListener("click", () => {
  if (!active) return;
  active.range = undefined;
  resetRangeMonths();
  byId("clear-range").hidden = true;
  byId("range-status").hidden = true;
  rebuildSelection(active);
});
byId("add-questions").addEventListener("click", () => {
  if (!active) return;
  for (const id of active.ids) selected.set(id, active.category);
  updateWorksheet();
  closePicker();
});
byId("select-questions").addEventListener("click", (event) => {
  if (active) printSelection(selectionFor(active.category, active.ids), active.answers, event.currentTarget);
});
byId("clear").addEventListener("click", () => { selected.clear(); updateWorksheet(); });
byId("worksheet").addEventListener("click", (event) => printSelection(new Map(selected), false, event.currentTarget));
byId("back").addEventListener("click", () => {
  byId("browser").hidden = false;
  byId("print-view").hidden = true;
  returnTo?.focus();
});
byId("include-title").addEventListener("change", updatePrintTitle);
byId("title-input").addEventListener("input", updatePrintTitle);
byId("switch-print").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await renderPrint(worksheetSession, !printingAnswers);
    printingAnswers = !printingAnswers;
  } finally {
    button.disabled = false;
  }
});
byId("print").addEventListener("click", () => window.print());
byId("close-question-detail").addEventListener("click", () => byId("question-detail-dialog").close());

function sectionFromUrl() {
  const id = window.location.hash.slice(1);
  return [...sectionButtons].some((button) => button.getAttribute("aria-controls") === id) ? id : directory[0].id;
}

function openSection(id) {
  closePicker(false);
  returnTo = undefined;
  byId("print-view").hidden = true;
  byId("browser").hidden = false;
  showSection(id);
}

buildDirectory();
showSection(sectionFromUrl());
for (const button of sectionButtons) {
  button.addEventListener("click", () => {
    if (preparingPrint) return;
    const id = button.getAttribute("aria-controls");
    const hash = "#" + id;
    if (window.location.hash !== hash) window.history.pushState(null, "", hash);
    openSection(id);
  });
}
window.addEventListener("hashchange", () => openSection(sectionFromUrl()));
populateDirectoryDates().catch(showError);
loadReleaseRows().catch((error) => {
  byId("release-summary").textContent = "Release filters unavailable";
  showError(error);
});
