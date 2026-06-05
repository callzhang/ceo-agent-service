import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const inputPath = "/Users/derek/Documents/Projects/ceo-agent-service/outputs/dingteam-okr-2026q2/dingteam_okr_2026q2_raw.json";
const outputDir = "/Users/derek/Documents/Projects/ceo-agent-service/outputs/dingteam-okr-2026q2";
const outputPath = path.join(outputDir, "dingteam_okr_2026q2.xlsx");
const previewPath = path.join(outputDir, "dingteam_okr_2026q2_summary.png");

const raw = JSON.parse(await fs.readFile(inputPath, "utf8"));

function normalizeText(value) {
  return String(value ?? "").replace(/\r/g, "").replace(/\n{3,}/g, "\n\n").trim();
}

function q2Text(profileText) {
  const text = normalizeText(profileText);
  const start = text.indexOf("2026年2季度");
  if (start < 0) return "";
  const next = text.indexOf("2026年1季度", start + 1);
  return text.slice(start, next > start ? next : undefined).trim();
}

function firstMatch(text, pattern) {
  const match = text.match(pattern);
  return match ? match[1] : "";
}

function parsePerson(profile) {
  const text = q2Text(profile.profileText);
  return {
    ...profile,
    q2Text: text || normalizeText(profile.profileText),
    q2TargetCount: Number(firstMatch(text, /目标数：\n(\d+)/)) || Number(profile.objectiveCount || 0),
    q2Progress: firstMatch(text, /进度：\n([\d.]+%)/) || "",
  };
}

function extractSections(text) {
  const normalized = normalizeText(text);
  const byAlign = normalized.split(/添加对齐\n\n/).slice(1);
  const cardSections = byAlign
    .map((section) => section.trim())
    .filter((section) => /\nO\d+\n进度\n权重/.test(`\n${section}`) || /^O\d+：/.test(section));
  if (cardSections.length) return cardSections;

  const lines = normalized.split("\n");
  const starts = [];
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i].trim();
    if (/^O\d+：/.test(line) || line === "文化价值观考核" || line === "领导力考核") {
      starts.push(i);
    }
  }
  return starts.map((start, i) => {
    const end = i + 1 < starts.length ? starts[i + 1] : lines.length;
    return lines.slice(start, end).join("\n").trim();
  });
}

function parseObjectiveSection(section, person) {
  const lines = section.split("\n").map((line) => line.trim()).filter(Boolean);
  const codeLineIndex = lines.findIndex((line) => /^O\d+$/.test(line));
  const titleCandidates = [];
  const titleEnd = codeLineIndex >= 0 ? codeLineIndex : lines.length;
  for (let i = 0; i < titleEnd; i += 1) {
    const line = lines[i];
    if (
      line === person.dept ||
      line === person.name ||
      line === "个人级" ||
      /^[\d.]+%$/.test(line)
    ) {
      break;
    }
    titleCandidates.push(line);
  }
  const titleLine = titleCandidates.join("\n") || lines[0] || "";
  const objectiveCode =
    firstMatch(titleLine, /^(O\d+)：/) ||
    firstMatch(section, /\n(O\d+)\n进度\n权重/) ||
    "";
  const title = titleLine.replace(/^O\d+：/, "").trim();
  const metrics = section.match(/进度\n权重\n([\d.]+%)\n([\d.]+%)/);
  const objectiveProgress = metrics ? metrics[1] : "";
  const objectiveWeight = metrics ? metrics[2] : "";
  return {
    personName: person.name,
    dept: person.dept,
    profileUserId: person.profileUserId || "",
    objectiveCode,
    objectiveTitle: title,
    objectiveProgress,
    objectiveWeight,
    rawObjectiveText: section,
  };
}

function parseKrs(section, objective) {
  const regex = /KR(\d+)：\n([\s\S]*?)(?=\nKR\d+：|$)/g;
  const rows = [];
  let match;
  while ((match = regex.exec(section)) !== null) {
    const raw = match[2].trim();
    const percentages = [...raw.matchAll(/(^|\n)([\d.]+%)\n([\d.]+%)(?=\n|$)/g)];
    const last = percentages.at(-1);
    let content = raw;
    let krProgress = "";
    let krWeight = "";
    if (last) {
      krProgress = last[2];
      krWeight = last[3];
      content = raw.slice(0, last.index).trim();
    }
    rows.push({
      ...objective,
      krCode: `KR${match[1]}`,
      krContent: content,
      krProgress,
      krWeight,
      rawKrText: raw,
    });
  }
  return rows;
}

const people = raw.profiles.map(parsePerson);
const objectives = [];
const krs = [];
for (const person of people) {
  for (const section of extractSections(person.q2Text)) {
    const objective = parseObjectiveSection(section, person);
    objectives.push(objective);
    krs.push(...parseKrs(section, objective));
  }
}

const workbook = Workbook.create();

function addSheet(name, rows, headerFill = "#2563EB") {
  const sheet = workbook.worksheets.add(name);
  if (!rows.length) return sheet;
  const width = rows[0].length;
  const height = rows.length;
  sheet.getRangeByIndexes(0, 0, height, width).values = rows;
  sheet.getRangeByIndexes(0, 0, 1, width).format = {
    fill: headerFill,
    font: { bold: true, color: "#FFFFFF" },
  };
  return sheet;
}

const source = raw.source;
const byPersonTargetCount = raw.people.reduce((sum, row) => sum + Number(row.objectiveCount || 0), 0);
addSheet("Summary", [
  ["Metric", "Value"],
  ["Source", source.system],
  ["Period", source.period],
  ["Period Range", source.periodRange],
  ["Generated At", raw.generatedAt],
  ["People Rows", people.length],
  ["Profiles Collected", raw.profiles.length],
  ["Cockpit Target Count", source.cockpitTargetCount],
  ["By-Person Objective Total", byPersonTargetCount],
  ["Cockpit Average Progress", source.cockpitAverageProgress],
  ["Weekly Change", source.cockpitWeeklyChange],
  ["Task Count", source.cockpitTaskCount],
  ["Task Completion Rate", source.cockpitTaskCompletionRate],
]);

addSheet("People Overview", [
  [
    "Index",
    "Name",
    "Department",
    "Cockpit Objectives",
    "Confirming",
    "Aligned",
    "Unaligned",
    "Q2 Header Objectives",
    "Q2 Progress",
    "Profile User ID",
    "Profile URL",
  ],
  ...people.map((p, idx) => [
    idx + 1,
    p.name,
    p.dept,
    Number(p.objectiveCount || 0),
    Number(p.confirmingCount || 0),
    Number(p.alignedCount || 0),
    Number(p.unalignedCount || 0),
    p.q2TargetCount,
    p.q2Progress,
    p.profileUserId || "",
    p.profileUrl || "",
  ]),
]);

addSheet("Objectives", [
  [
    "Person",
    "Department",
    "Objective Code",
    "Objective Title",
    "Objective Progress",
    "Objective Weight",
    "Profile User ID",
    "Raw Objective Text",
  ],
  ...objectives.map((row) => [
    row.personName,
    row.dept,
    row.objectiveCode,
    row.objectiveTitle,
    row.objectiveProgress,
    row.objectiveWeight,
    row.profileUserId,
    row.rawObjectiveText,
  ]),
]);

addSheet("KR Details", [
  [
    "Person",
    "Department",
    "Objective Code",
    "Objective Title",
    "KR Code",
    "KR Content",
    "KR Progress",
    "KR Weight",
    "Profile User ID",
  ],
  ...krs.map((row) => [
    row.personName,
    row.dept,
    row.objectiveCode,
    row.objectiveTitle,
    row.krCode,
    row.krContent,
    row.krProgress,
    row.krWeight,
    row.profileUserId,
  ]),
]);

addSheet("Raw Q2 Text", [
  ["Person", "Department", "Q2 Text", "Profile User ID", "Profile URL"],
  ...people.map((p) => [p.name, p.dept, p.q2Text, p.profileUserId || "", p.profileUrl || ""]),
]);

const noDetail = people.filter((p) => Number(p.objectiveCount || 0) === 0 || !p.profileUserId || !p.q2Text.includes("2026年2季度"));
addSheet("Data Quality", [
  ["Check", "Result"],
  ["Cockpit objective count", source.cockpitTargetCount],
  ["Sum of objectives in by-person table", byPersonTargetCount],
  ["Difference", Number(source.cockpitTargetCount) - byPersonTargetCount],
  ["People with no Q2 OKR detail", noDetail.map((p) => p.name).join(", ") || "None"],
  ["Collection note", "Data was collected from the logged-in Dingteam OKR Chrome page. Raw Q2 text is retained for audit."],
]);

const preview = await workbook.render({
  sheetName: "Summary",
  autoCrop: "all",
  scale: 1,
  format: "png",
});
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));

const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);

console.log(JSON.stringify({
  outputPath,
  previewPath,
  people: people.length,
  objectives: objectives.length,
  krs: krs.length,
  byPersonTargetCount,
}, null, 2));
