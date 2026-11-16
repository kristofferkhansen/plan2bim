// The page of the plan2bim web service. It talks to /api (see plan2bim/service.py). No library, no build step.
"use strict";

const main = document.getElementById("main");
const HEIGHTS = [["plate", "Floor and roof plate"], ["door_head", "Door head"], ["window_sill", "Window sill"],
                 ["window_head", "Window head"], ["landing", "Stair landing"], ["balustrade", "Balustrade"]];
const LAYERS = [["lines", "Building lines"], ["rooms", "Room outlines and labels"], ["gross", "Gross outline"],
                ["inner", "Inner face of exterior walls"], ["ignore", "Layers that are not read"]];
const TAGS = [["room_number", "Room number tag"], ["room_name", "Room name tag"], ["room_wing", "Wing tag"]];
const TEXTS = [["room_number_text", "Layers with the room number as a text"],
               ["room_name_text", "Layers with the room name as a text"]];
const WORDS = [["shafts", "Room names of lifts and shafts"], ["cores", "Words in names of rooms through all storeys"]];
const STATES = {building: "building", rejected: "cannot be built", error: "stopped"};
const RESULTS = {pass: "passed", warn: "passed, with points to look at", fail: "a check failed"};
const MORE = `# Optional. The same tables as in a plan2bim project file. Coordinates are in mm, in the frame of the reference storey.
# [[stair]]
# name = "A"
# kind = "straight"                # straight, dogleg or spiral
# levels = ["1"]                   # storeys from which this stair climbs one storey
# box = [1200, 6400, 6000, 7700]   # the shaft: x0, y0, x1, y1
# up = "E"                         # direction of climb: N, S, E or W
`;
let defaults = null;
let timer = 0;
const STOREY = 3000;        // mm: the height that the page gives to a storey, until the user gives the levels

// The storey that the name of a drawing gives, or null: its place from the lowest, a short label, its name.
// "1.etasje", "2 etg", "plan 3", "level 4", "U.etasje", "U1 etg", "plan U", "kjeller", "sokkeletasje", and the
// storey after the number of the building: "12345678_01", "12345678_U1_plan".
function storeyOf(fileName) {
  const stem = fileName.replace(/\.[^.]+$/, "");
  const named = (match) => match[0].replace(/^[^a-z0-9]/i, "").trim();
  const below = new RegExp("(?:^|[^a-z0-9])(?:([uk])(\\d?)[\\s._-]*(?:etasje|etg)|(?:etasje|etg|plan)[\\s._-]*([uk])(\\d?)"
    + "|(underetasje|sokkel(?:etasje)?|kjeller(?:etasje)?|basement)[\\s._-]*(\\d?))(?![a-z0-9])", "i").exec(stem);
  if (below) {
    const cellar = /^[kb]/i.test(below[1] || below[3] || below[5]);       // a cellar lies below an "underetasje"
    const number = below[2] || below[4] || below[6] || "";
    return {rank: -(cellar ? 10 : 0) - Number(number || 1), key: (cellar ? "K" : "U") + number, name: named(below)};
  }
  const above = /(?:^|[^a-z0-9])(?:(\d{1,2})[\s._-]*(?:etasje|etg|floor|storey|level)|(?:etasje|etg|floor|storey|level|plan)[\s._-]*(\d{1,2}))(?![a-z0-9])/i
    .exec(stem);
  if (above) {
    const number = Number(above[1] || above[2]);
    return {rank: number, key: String(number), name: named(above)};
  }
  const coded = /^\d{5,}_(?:(\d{1,2})|([uk])(\d?))(?:_|$)/i.exec(stem);
  if (coded && coded[1]) return {rank: Number(coded[1]), key: String(Number(coded[1])), name: `Etasje ${coded[1]}`};
  if (coded) {
    const cellar = /k/i.test(coded[2]);
    const key = (cellar ? "K" : "U") + coded[3];
    return {rank: -(cellar ? 10 : 0) - Number(coded[3] || 1), key, name: `Etasje ${key}`};
  }
  return null;
}

// The number that a person typed, with a decimal comma or a decimal point and with or without spaces between
// the thousands. Null: the text is empty or is not such a number.
function numberOf(text) {
  const plain = String(text).replace(/[\s\u00a0]/g, "");
  return /^-?\d+(?:[.,]\d+)?$/.test(plain) ? Number(plain.replace(",", ".")) : null;
}

// A box for a number. Not `type=number`: a browser with an English locale takes "3100,5" there as 31005 and
// says nothing. The form is not sent while a box holds a text that is not a number.
function numberBox(props) {
  const box = el("input", {type: "text", inputMode: "decimal", class: "number", autocomplete: "off", ...props});
  box.addEventListener("input", () => {
    const wrong = box.value.trim() !== "" && numberOf(box.value) === null;
    box.setCustomValidity(wrong ? "Give a number, for example 3000 or 2880,5." : "");
    box.classList.toggle("wrong", wrong);
  });
  return box;
}

function el(tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (key === "class") e.className = value;
    else if (key.startsWith("on")) e.addEventListener(key.slice(2), value);
    else if (key in e) e[key] = value;
    else e.setAttribute(key, value);
  }
  e.append(...kids.flat().filter((kid) => kid !== null && kid !== undefined && kid !== false));
  return e;
}

async function api(method, path, body) {
  let response;
  try {
    response = await fetch(path, {method, body, headers: method === "GET" ? {} : {"X-Plan2bim": "1"}});
  } catch {
    throw new Error("No answer from the service. You are perhaps signed out: load the page again.");
  }
  if (response.status === 401) throw new Error("You are not signed in. Load the page again.");
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof data.detail === "string" ? data.detail : `The service answered ${response.status}.`);
    error.problems = data.problems;
    throw error;
  }
  return data;
}

function fail(error) {
  main.replaceChildren(el("div", {class: "card"}, el("ul", {class: "problems"}, el("li", {}, error.message)),
    el("a", {class: "button", href: "#/"}, "All projects")));
}

function problemList(problems) {
  return el("ul", {class: "problems"}, problems.map((text) => el("li", {}, text)));
}

function badge(project) {
  const status = project.state === "done" ? (project.status || (project.result || {}).status) : project.state;
  return el("span", {class: `badge ${status}`}, RESULTS[status] || STATES[status] || status);
}

const files = (id, name) => `/api/projects/${id}/files/${name}`;

// ---------------------------------------------------------------- all projects

async function list() {
  const projects = await api("GET", "/api/projects");
  main.replaceChildren(
    el("div", {class: "title"}, el("h1", {}, "Projects"), el("a", {class: "button main", href: "#/new"}, "New project")),
    projects.length === 0
      ? el("div", {class: "card"}, el("p", {class: "lead"},
          "Upload the floor plan drawings (DWG or DXF) of each storey of a building. The service builds a 3D model ",
          "from them, shows it here, and gives you the model as an IFC file."))
      : el("div", {class: "card"}, el("table", {},
          el("tr", {}, el("th", {}, "Project"), el("th", {}, "Made"), el("th", {}, "Result")),
          projects.map((p) => el("tr", {class: "link", onclick: () => { location.hash = `#/p/${p.id}`; }},
            el("td", {}, el("a", {href: `#/p/${p.id}`}, p.name)),
            el("td", {}, new Date(p.created).toLocaleString()), el("td", {}, badge(p)))))));
}

// ---------------------------------------------------------------- one project

async function show(id) {
  const p = await api("GET", `/api/projects/${id}`);
  const again = async () => {
    const form = new FormData();
    form.append("definition", JSON.stringify(p.definition));
    form.append("more", p.more);
    try { await api("PUT", `/api/projects/${id}`, form); route(); } catch (error) { fail(error); }
  };
  const remove = async () => {
    if (!confirm(`Remove "${p.name}", its drawings and its model?`)) return;
    try { await api("DELETE", `/api/projects/${id}`); location.hash = "#/"; } catch (error) { fail(error); }
  };
  const parts = [el("div", {class: "title"}, el("h1", {}, p.name), badge(p), el("span", {class: "grow"}),
    p.state !== "building" && el("a", {class: "button", href: `#/p/${id}/edit`}, "Edit drawings and levels"),
    el("button", {class: "danger", onclick: remove}, "Remove"))];

  if (p.state === "building") {
    parts.push(el("div", {class: "card row"}, el("span", {class: "spinner"}),
      el("span", {}, `Building the model. ${p.progress || ""}`)));
    timer = setTimeout(route, 2000);
  } else if (p.state === "rejected") {
    parts.push(el("div", {class: "card"}, el("strong", {}, "plan2bim cannot build this project. Change this:"),
      problemList(p.problems)));
  } else if (p.state === "error") {
    parts.push(el("div", {class: "card"}, problemList([p.message]),
      el("button", {class: "main", onclick: again}, "Build again")));
  } else {
    const r = p.result;
    const counts = Object.entries(r.elements).map(([kind, n]) => `${n} ${kind}${n === 1 ? "" : "s"}`).join(", ");
    const notes = el("div", {});
    parts.push(
      el("div", {class: "card"},
        el("pre", {}, r.summary.join("\n")),
        el("p", {class: "muted"}, `${r.rooms} rooms. ${counts}.`),
        el("div", {class: "row"},
          el("a", {class: "button main", href: files(id, "ifc"), download: ""}, "Save as IFC"),
          el("a", {class: "button", href: files(id, "viewer"), target: "_blank"}, "Open the 3D model in a tab"),
          el("a", {class: "button", href: files(id, "report"), target: "_blank"}, "Report of the checks")),
        notes),
      el("iframe", {class: "viewer", src: files(id, "viewer"), title: `3D model of ${p.name}`}));
    const plots = Object.keys(p.files).filter((what) => what.startsWith("plot "));
    if (plots.length) {
      parts.push(el("h2", {}, "Each storey, with what every cell of the drawing was taken to be"),
        el("div", {class: "plots"}, plots.map((what) => {
          const storey = what.slice(5);
          const url = `${files(id, `plot-${storey}`)}?build=${p.build}`;    // a new URL after each build: no old image
          return el("a", {href: url, target: "_blank"}, el("img", {src: url, alt: `Storey ${storey}`, loading: "lazy"}),
            `Storey ${storey}`);
        })));
    }
    if (r.counts.fail + r.counts.warn > 0) {
      api("GET", files(id, "data")).then((data) => {
        const look = data.notes.filter((n) => n.severity !== "info");
        notes.append(el("details", {}, el("summary", {}, `Failed checks and points to look at (${look.length})`),
          el("ul", {class: "notes"}, look.map((n) => el("li", {class: n.severity}, n.text)))));
      }).catch(() => {});
    }
  }
  main.replaceChildren(...parts.filter(Boolean));
}

// ---------------------------------------------------------------- new project, changed project

async function edit(id) {
  defaults = defaults || await api("GET", "/api/defaults");
  const p = id ? await api("GET", `/api/projects/${id}`) : null;
  const d = p ? p.definition : {name: "", reference: "", heights: {}, input: {}, level: []};
  const names = p ? Object.fromEntries(p.drawings.map((drawing) => [drawing.file, drawing.name])) : {};
  // the place that the file name gives is kept for a stored drawing too: the form warns of levels that contradict it
  const rows = d.level.map((lv) => {
    const drawings = [lv.drawing].flat().map((drawing) => ({drawing, label: names[drawing] || drawing, file: null}));
    return {key: lv.key, name: lv.name, elevation: lv.elevation, drawings,
      rank: (storeyOf(drawings[0].label) || {rank: null}).rank, guess: false};
  });
  let roofSet = Boolean(p);
  let levelsSet = Boolean(p);       // the user gave a level: the page puts no storey in another place any more
  let referenceSet = Boolean(p);
  let chosen = rows.find((row) => row.key === d.reference) || null;     // the row of the reference storey

  const name = el("input", {type: "text", value: d.name, required: true});
  const roof = numberBox({value: p ? d.heights.roof : "", required: true, oninput: () => { roofSet = true; sync(); }});
  const reference = el("select", {onchange: () => { referenceSet = true; chosen = rows[reference.value]; }});
  const heights = Object.fromEntries(HEIGHTS.map(([key]) =>
    [key, numberBox({value: (d.heights || {})[key] ?? defaults.heights[key]})]));
  const input = d.input || {};
  const text = (key) => el("input", {type: "text", value: [input[key] ?? defaults.input[key]].flat().join(", ")});
  const unit = el("select", {}, ["mm", "cm", "m"].map((u) => el("option", {value: u}, u)));
  unit.value = input.unit || defaults.input.unit;
  const fields = Object.fromEntries([...LAYERS, ...TAGS, ...TEXTS, ...WORDS].map(([key]) => [key, text(key)]));
  const more = el("textarea", {value: p ? p.more : MORE, spellcheck: false});
  const table = el("tbody", {});
  const sheet = el("div", {class: "sheet"});
  const caution = el("div", {class: "caution"});
  const errors = el("div", {});
  const sendText = p ? "Save and build again" : "Build the model";
  const send = el("button", {class: "main", type: "submit"}, sendText);

  const sync = () => {
    if (!referenceSet || !rows.includes(chosen)) chosen = rows.find((row) => row.rank === 1) || rows[0] || null;
    reference.replaceChildren(...rows.map((row, i) => el("option", {value: i}, row.key || "?")));
    reference.value = String(rows.indexOf(chosen));
    const levels = rows.map((row) => row.elevation).filter((level) => level !== null).sort((a, b) => a - b);
    if (!roofSet) {
      const storey = levels.length > 1 ? levels[levels.length - 1] - levels[levels.length - 2] : STOREY;
      roof.value = levels.length ? levels[levels.length - 1] + storey : "";
    }
    const over = numberOf(roof.value);
    for (const row of rows) {
      const next = levels.find((level) => level > row.elevation) ?? (over ?? NaN);
      const height = row.elevation === null ? NaN : next - row.elevation;
      row.height.textContent = height > 0 ? String(height) : "\u2014";
    }
    const twice = (values) => [...new Set(values.filter((value, i) => values.indexOf(value) !== i))];
    const unplaced = rows.filter((row) => row.rank === null && row.drawings.some((one) => one.file))
      .map((row) => row.drawings[0].label);
    const placed = rows.filter((row) => row.rank !== null && row.elevation !== null)
      .sort((a, b) => a.elevation - b.elevation);
    const wrong = placed.findIndex((row, i) => i > 0 && row.rank < placed[i - 1].rank);
    const top = levels[levels.length - 1];
    caution.replaceChildren(...[
      unplaced.length && `No storey in the file name of: ${unplaced.join(", ")}. Give the label and the floor level yourself.`,
      wrong > 0 && `By the file names, ${placed[wrong].drawings[0].label} lies below ${placed[wrong - 1].drawings[0].label}, but its floor level `
        + "is higher. Check the floor levels.",
      twice(rows.map((row) => row.key)).map((key) => `Two storeys have the label \u201c${key}\u201d. Each needs its own.`),
      twice(levels).map((level) => `Two storeys have the floor level ${level}. Each needs its own.`),
      over !== null && top >= over && "The top of the roof must be above the highest floor level.",
    ].flat().filter(Boolean).map((line) => el("p", {}, line)));
  };
  const drawn = (name) => rows.some((row) => row.drawings.some((item) => item.drawing === name));
  const item = (file) => ({drawing: file.name, label: file.name, file});
  const draw = () => {
    table.replaceChildren(...rows.map((row) => {
      row.height = el("td", {class: "muted"});
      return el("tr", {},
        el("td", {class: "drawings"},
          row.drawings.map((one) => el("div", {}, one.label, " ", row.drawings.length > 1 && el("button", {type: "button",
            class: "small", title: "Take this drawing out of the storey",
            onclick: () => { row.drawings.splice(row.drawings.indexOf(one), 1); draw(); }}, "\u00d7"))),
          el("label", {class: "more", title: "Another drawing of this storey, with the same coordinates"}, "+ drawing",
            el("input", {type: "file", multiple: true, accept: ".dwg,.dxf", hidden: true, onchange: (e) => {
              row.drawings.push(...[...e.target.files].filter((file) => !drawn(file.name)).map(item));
              draw();
            }}))),
        el("td", {}, el("input", {type: "text", value: row.key, size: 4, required: true, pattern: "[A-Za-z0-9_.\\-]+",
          title: "Letters, digits, \u2018.\u2019, \u2018_\u2019 or \u2018-\u2019",
          oninput: (e) => { row.key = e.target.value.trim(); row.guess = false; sync(); }})),
        el("td", {}, el("input", {type: "text", class: "name", value: row.name, required: true,
          oninput: (e) => { row.name = e.target.value; }})),
        el("td", {}, numberBox({value: row.elevation ?? "", required: true,
          oninput: (e) => {
            row.elevation = numberOf(e.target.value);
            levelsSet = true;
            sync();
          }})),
        row.height,
        el("td", {}, el("button", {type: "button", class: "small", title: "Take this storey out",
          onclick: () => { rows.splice(rows.indexOf(row), 1); place(); draw(); }}, "\u00d7")));
    }));
    sheet.hidden = rows.length === 0;
    sync();
  };
  // Until the user gives a level, the page lists the storeys from the lowest to the highest and gives the levels:
  // first those that the file names place, then the others in the order of their names. The page numbers the
  // storeys that the file names do not give above the others, and it gives them new numbers when the rows move.
  const place = () => {
    if (!levelsSet) {
      rows.sort((a, b) => (a.rank === null) - (b.rank === null) || a.rank - b.rank
        || a.drawings[0].label.localeCompare(b.drawings[0].label, undefined, {numeric: true}));
      rows.forEach((row, i) => { row.elevation = i * STOREY; });
    }
    const renew = (row) => row.guess && (row.key === null || !levelsSet);
    let number = Math.max(0, ...rows.filter((row) => !renew(row)).map((row) => Number(row.key)).filter(Number.isInteger));
    for (const row of rows.filter(renew)) row.key = String(number += 1);
  };
  const add = (event) => {
    const picked = [...event.target.files].sort((a, b) => a.name.localeCompare(b.name, undefined, {numeric: true}));
    for (const file of picked) {
      if (drawn(file.name)) continue;
      const found = storeyOf(file.name);
      const same = found && rows.find((row) => !row.guess && row.rank === found.rank && row.key === found.key);
      if (same) {                 // the storey has this drawing too: an area drawing and its plan drawing
        same.drawings.push(item(file));
        continue;
      }
      const top = rows.length ? Math.max(...rows.map((row) => Number(row.elevation))) + STOREY : 0;
      let key = found ? found.key : null;
      for (const row of rows) if (found && row.guess && row.key === key) row.key = null;   // the file name wins
      for (let n = 1; key !== null && rows.some((row) => row.key === key); n += 1) key = found.key + "abcdefghij"[n];
      rows.push({key, name: found ? found.name : file.name.replace(/\.[^.]+$/, ""), drawings: [item(file)], elevation: top,
        rank: found ? found.rank : null, guess: !found});
    }
    place();
    event.target.value = "";
    draw();
  };
  const submit = async (event) => {
    event.preventDefault();
    const list = (key) => fields[key].value.split(",").map((part) => part.trim()).filter(Boolean);
    const number = (box) => numberOf(box.value);
    const definition = {
      name: name.value.trim(), reference: chosen ? chosen.key : "",
      heights: {roof: number(roof), ...Object.fromEntries(HEIGHTS.map(([key]) => [key, number(heights[key])]))},
      input: {unit: unit.value, ...Object.fromEntries([...LAYERS, ...TEXTS, ...WORDS].map(([key]) => [key, list(key)])),
        ...Object.fromEntries(TAGS.map(([key]) => [key, fields[key].value.trim()]))},
      level: rows.map((row) => ({key: row.key, name: row.name, drawing: row.drawings.map((one) => one.drawing),
        elevation: row.elevation})),
    };
    const form = new FormData();
    form.append("definition", JSON.stringify(definition));
    form.append("more", more.value);
    for (const row of rows) for (const one of row.drawings) if (one.file) form.append("drawings", one.file, one.file.name);
    send.disabled = true;
    send.textContent = "Sending the drawings";
    try {
      const made = await api(p ? "PUT" : "POST", p ? `/api/projects/${id}` : "/api/projects", form);
      if (location.hash === `#/p/${made.id}`) route(); else location.hash = `#/p/${made.id}`;
    } catch (error) {
      errors.replaceChildren(problemList(error.problems || [error.message]));
      send.disabled = false;
      send.textContent = sendText;
    }
  };
  const field = (label, box, wide) => el("label", {class: wide ? "field wide" : "field"}, label, box);

  sheet.append(
    el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Drawings"), el("th", {}, "Storey"),
      el("th", {}, "Name in the model"), el("th", {}, "Floor level, mm"), el("th", {}, "Height to the floor above, mm"),
      el("th", {}))), table),
    caution,
    el("ul", {class: "help"},
      el("li", {}, el("b", {}, "Drawings: "), "the drawings of one storey are read as one drawing, so they must have the ",
        "same coordinates. \u201c+ drawing\u201d adds a file to a storey. A storey needs one plan only. When a storey ",
        "has two plans, for example the plan for facility management and the plan of the architect, the plan on which ",
        "the room outlines were drawn is read, and the report names the drawing that is not read."),
      el("li", {}, el("b", {}, "Storey: "), "a short label, for example K, U, 1 or 2, different for each storey. Use only ",
        "letters, digits, \u2018.\u2019, \u2018_\u2019 and \u2018-\u2019. The report and the names of the output files use it."),
      el("li", {}, el("b", {}, "Name in the model: "), "the name of the storey in the 3D model and in the IFC file."),
      el("li", {}, el("b", {}, "Floor level: "), "the height of the top of the floor in mm, from the same zero for all ",
        "storeys, for example the floor of the lowest storey. The levels give the order of the storeys, not the order of ",
        `the rows. A plan drawing holds no height, so the page gives ${STOREY} mm to each storey: take the real levels `,
        "from a section drawing. Once you type a level, the page moves no row, and a drawing added later goes on top."),
      el("li", {}, el("b", {}, "Height to the floor above: "), "what the levels give, to check them. For the highest ",
        "storey: the height to the top of the roof. A dash: the levels give no height.")));
  draw();
  const form = el("form", {onsubmit: submit},
    el("div", {class: "title"}, el("h1", {}, p ? `Edit ${p.name}` : "New project")),
    el("div", {class: "card"},
      el("div", {class: "row"}, field("Name of the building", name, true)),
      el("h2", {}, "Drawings of each storey"),
      el("p", {class: "muted"}, "Select the DWG or DXF files of the storeys. A storey has one drawing, or two when ",
        "the outlines of the rooms and of the storey are in an area drawing of their own: then give the area drawing ",
        "and the plan drawing with the walls. The page reads the storey from the name of the file, for example ",
        "\u201c1.etasje\u201d, \u201cU.etasje\u201d, \u201cplan 2\u201d or \u201c12345678_01\u201d, puts the files of one storey together and ",
        "lists the storeys from the lowest to the highest. A drawing does not say which storey it is or how high it ",
        "lies: check the labels and the floor levels before you build."),
      el("p", {}, el("input", {type: "file", multiple: true, accept: ".dwg,.dxf", onchange: add}),
        el("span", {class: "muted"}, ` DWG or DXF, ${defaults.max_drawing_mb} MB each at most.`)),
      el("div", {}, sheet),
      el("div", {class: "row"}, field("Level of the top of the roof, mm, from the same zero", roof),
        field("Reference storey: the drawings of the other storeys are moved to line up with its drawing", reference))),
    el("div", {class: "card"},
      el("details", {}, el("summary", {}, "Assumed heights, mm"),
        el("div", {class: "row"}, HEIGHTS.map(([key, label]) => field(label, heights[key])))),
      el("details", {}, el("summary", {}, "Layers and unit of the drawings"),
        el("p", {class: "muted"}, "Give several layers with a comma between them. A \u201c*\u201d stands for any text: ",
          "\u201cA2*\u201d is every layer whose name starts with A2, and \u201c*\u201d alone is every layer that no other box ",
          "names. The defaults read two conventions of Norwegian drawings for facility management: room and gross ",
          "outlines on Area_NTA and Area_BTA with a label block in each room, or on \u201c862 - Rom NTA\u201d and ",
          "\u201c861 - Etasje BTA\u201d with the room number and name as texts."),
        el("div", {class: "row"}, field("Unit", unit), LAYERS.map(([key, label]) => field(label, fields[key]))),
        el("div", {class: "row"}, TAGS.map(([key, label]) => field(label, fields[key])),
          TEXTS.map(([key, label]) => field(label, fields[key]))),
        el("div", {class: "row"}, WORDS.map(([key, label]) => field(label, fields[key], true)))),
      el("details", {}, el("summary", {}, "More settings: stairs, review decisions, gates, map position"), more)),
    errors,
    el("div", {class: "row"}, send, el("a", {class: "button", href: p ? `#/p/${id}` : "#/"}, "Cancel")));
  // a box that is not right can lie in a closed part of the form: open it, so that the browser can show the box
  form.addEventListener("invalid", (event) => {
    const part = event.target.closest("details");
    if (part) part.open = true;
  }, true);
  main.replaceChildren(form);
}

// ---------------------------------------------------------------- which view

async function route() {
  clearTimeout(timer);
  const [, view, id, what] = location.hash.split("/");
  try {
    if (view === "new") await edit(null);
    else if (view === "p" && what === "edit") await edit(id);
    else if (view === "p") await show(id);
    else await list();
  } catch (error) {
    fail(error);
  }
}

window.addEventListener("hashchange", route);
api("GET", "/api/me").then((me) => {
  document.getElementById("user").textContent = me.name;
  if (me.sign_out) Object.assign(document.getElementById("signout"), {hidden: false, href: me.sign_out});
  route();
}).catch(fail);
