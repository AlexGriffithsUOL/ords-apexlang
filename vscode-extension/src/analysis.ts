// Editor features over a parsed .apx document and the grammar's schema.
// Nothing here depends on the vscode module, so it can be unit tested.

import { ApxComponent, ApxDocument, ApxGroup, ApxProperty, ApxValue, Problem, Span } from "./apxDocument";
import { ComponentSpec, PropertySpec, Schema } from "./schema";

const CAMEL_RE = /^[a-z][a-z0-9]*([A-Z][a-z0-9]*)*$/;

function toParam(key: string): string {
  return "p_" + key.replace(/(?<!^)(?=[A-Z])/g, "_").toLowerCase();
}

function distance(a: string, b: string): number {
  const d = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array(b.length).fill(0)]);
  for (let j = 1; j <= b.length; j++) d[0][j] = j;
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
  }
  return d[a.length][b.length];
}

function suggest(word: string, options: Iterable<string>): string {
  let best: string | undefined;
  let bestScore = Infinity;
  for (const o of options) {
    const score = distance(word.toLowerCase(), o.toLowerCase());
    if (score < bestScore) [best, bestScore] = [o, score];
  }
  return best !== undefined && bestScore <= Math.max(2, Math.floor(word.length / 3)) ? ` Did you mean '${best}'?` : "";
}

function* walk(components: ApxComponent[]): Generator<ApxComponent> {
  for (const c of components) {
    yield c;
    yield* walk(c.children);
  }
}

/** Top-level components that a @reference can point to, by id. */
function referenceTargets(doc: ApxDocument): Map<string, ApxComponent> {
  const out = new Map<string, ApxComponent>();
  for (const c of doc.components) if (c.id !== undefined && !out.has(c.id)) out.set(c.id, c);
  return out;
}

// --- diagnostics ------------------------------------------------------------

function checkValue(prop: ApxProperty, spec: PropertySpec, targets: Map<string, ApxComponent>, problems: Problem[]) {
  const v = prop.value;
  if (!v) return;
  const err = (message: string, span: Span = v.span) => problems.push({ message, span, severity: "error" });
  switch (spec.type) {
    case "boolean":
      if (v.kind !== "boolean") err(`'${spec.key}' must be true or false`);
      break;
    case "number":
      if (v.kind !== "number") err(`'${spec.key}' must be a number`);
      break;
    case "enum":
      if (v.kind === "quoted") break;
      if (v.kind !== "string" || !spec.values.includes(v.text)) {
        err(`'${v.text}' is not a valid ${spec.key}: expected ${spec.values.join(", ")} (or a "quoted" ORDS value).` +
          suggest(v.text, spec.values));
      }
      break;
    case "names":
      if (v.kind !== "array") {
        err(`'${spec.key}' must be an array, e.g. ${spec.key}: [a b]`);
        break;
      }
      for (const item of v.items ?? []) {
        if (item.kind === "reference" && !targets.has(item.text)) {
          problems.push({ message: `no component named '${item.text}' in this file`, span: item.span, severity: "warning" });
        } else if (item.kind === "boolean" || item.kind === "array") {
          err(`'${item.text}' is not a name; quote it`, item.span);
        }
      }
      break;
    case "text":
    case "code":
      if (v.kind === "array" || v.kind === "reference") err(`'${spec.key}' takes text, not ${v.kind === "array" ? "an array" : "a reference"}`);
      break;
  }
}

function checkProperties(
  props: ApxProperty[],
  specs: Map<string, PropertySpec>,
  where: string,
  passThrough: boolean,
  targets: Map<string, ApxComponent>,
  problems: Problem[],
) {
  for (const prop of props) {
    const spec = specs.get(prop.key);
    if (spec) {
      checkValue(prop, spec, targets, problems);
    } else if (passThrough && CAMEL_RE.test(prop.key)) {
      problems.push({
        message: `'${prop.key}' is not a known ${where} property; it will be passed to ORDS as ${toParam(prop.key)}.` +
          suggest(prop.key, specs.keys()),
        span: prop.keySpan,
        severity: "warning",
      });
    } else {
      problems.push({
        message: `unknown ${where} property '${prop.key}'.` + suggest(prop.key, specs.keys()),
        span: prop.keySpan,
        severity: "error",
      });
    }
  }
}

export function validate(doc: ApxDocument, schema: Schema): Problem[] {
  const problems = [...doc.problems];
  const targets = referenceTargets(doc);
  const seenTop = new Map<string, Set<string>>();

  const checkComponent = (c: ApxComponent, parentSpec: ComponentSpec | undefined) => {
    const spec = schema.components.get(c.kind);
    const allowed = parentSpec ? parentSpec.children : schema.topLevel;
    if (!spec) {
      problems.push({
        message: `unknown component '${c.kind}'.` + suggest(c.kind, allowed),
        span: c.kindSpan,
        severity: "error",
      });
      return;
    }
    if (!allowed.includes(c.kind)) {
      problems.push({
        message: parentSpec
          ? `'${c.kind}' cannot be nested inside ${parentSpec.kind}` +
            (parentSpec.children.length ? ` (allowed: ${parentSpec.children.join(", ")})` : "")
          : `'${c.kind}' must be nested inside ${[...schema.components.values()].filter((s) => s.children.includes(c.kind)).map((s) => s.kind).join(" or ")}`,
        span: c.kindSpan,
        severity: "error",
      });
    }
    if (spec.id === "required" && c.id === undefined) {
      problems.push({ message: `${c.kind} needs an id (${spec.idParam ?? "name"}), e.g. ${c.kind} name (`, span: c.kindSpan, severity: "error" });
    } else if (spec.id === "none" && c.id !== undefined) {
      problems.push({ message: `${c.kind} does not take an id`, span: c.idSpan!, severity: "error" });
    }
    checkProperties(c.properties, spec.properties, c.kind, spec.passThrough, targets, problems);
    for (const g of c.groups) {
      const gspec = spec.groups.get(g.name);
      if (!gspec) {
        problems.push({
          message: `unknown ${c.kind} group '${g.name}'` +
            (spec.groups.size ? ` (groups: ${[...spec.groups.keys()].join(", ")}).` : ".") + suggest(g.name, spec.groups.keys()),
          span: g.nameSpan,
          severity: "error",
        });
        continue;
      }
      checkProperties(g.properties, gspec.properties, `${c.kind} ${g.name}`, false, targets, problems);
    }
    const groupNames = new Set<string>();
    for (const g of c.groups) {
      if (groupNames.has(g.name)) problems.push({ message: `duplicate group '${g.name}'`, span: g.nameSpan, severity: "error" });
      groupNames.add(g.name);
    }
    // the converter maps sqlQuery and plsqlCode to the same parameter
    const params = new Map<string, string>();
    for (const [props, specs] of [[c.properties, spec.properties] as const, ...c.groups.map((g) => [g.properties, spec.groups.get(g.name)?.properties] as const)]) {
      for (const p of props) {
        const param = specs?.get(p.key)?.param;
        if (!param) continue;
        if (params.has(param)) {
          problems.push({ message: `'${p.key}' and '${params.get(param)}' both set ${param}; keep one`, span: p.keySpan, severity: "error" });
        }
        params.set(param, p.key);
      }
    }
    const childIds = new Map<string, Set<string>>();
    for (const child of c.children) {
      checkComponent(child, spec);
      if (child.id === undefined) continue;
      const ids = childIds.get(child.kind) ?? new Set();
      const id = child.kind === "handler" ? child.id.toUpperCase() : child.id;
      if (ids.has(id)) problems.push({ message: `duplicate ${child.kind} '${child.id}'`, span: child.idSpan!, severity: "error" });
      ids.add(id);
      childIds.set(child.kind, ids);
    }
  };

  for (const c of doc.components) {
    checkComponent(c, undefined);
    if (c.id === undefined && c.kind !== "restSchema") continue;
    const ids = seenTop.get(c.kind) ?? new Set();
    const id = c.id ?? "";
    if (ids.has(id)) problems.push({ message: `duplicate ${c.kind} '${c.id ?? ""}'`, span: c.idSpan ?? c.kindSpan, severity: "error" });
    ids.add(id);
    seenTop.set(c.kind, ids);
  }
  return problems;
}

// --- cursor context -----------------------------------------------------------

export type Context =
  | { type: "top" }
  | { type: "component"; component: ApxComponent }
  | { type: "group"; component: ApxComponent; group: ApxGroup }
  | { type: "value"; component: ApxComponent; group?: ApxGroup; key: string; prefix: string; inArray: boolean }
  | { type: "code" };

function containerAt(components: ApxComponent[], line: number): ApxComponent | undefined {
  for (const c of components) {
    if (line > c.startLine && line <= c.endLine) {
      return containerAt(c.children, line) ?? c;
    }
  }
  return undefined;
}

export function contextAt(doc: ApxDocument, line: number, character: number): Context {
  if (doc.fences.some((f) => line > f.start && line < f.end)) return { type: "code" };
  const text = (doc.lines[line] ?? "").slice(0, character);
  const component = containerAt(doc.components, line);
  if (!component) return { type: "top" };
  // an unclosed group ends on its last content line rather than on a '}'
  const group = component.groups.find(
    (g) => line > g.startLine && (line < g.endLine || (line === g.endLine && doc.lines[line].trim() !== "}")),
  );
  const m = /^\s*([A-Za-z][A-Za-z0-9]*):\s(.*)$/.exec(text);
  if (m) {
    const prefix = m[2];
    const inArray = prefix.includes("[") && !prefix.includes("]");
    return { type: "value", component, group, key: m[1], prefix: inArray ? prefix.slice(prefix.lastIndexOf("[") + 1).split(/\s+/).pop()! : prefix, inArray };
  }
  return group ? { type: "group", component, group } : { type: "component", component };
}

// --- completion ----------------------------------------------------------------

export interface Completion {
  label: string;
  kind: "component" | "property" | "group" | "value" | "reference";
  /** Snippet syntax; \t is one indentation level. */
  insertText: string;
  detail?: string;
  documentation?: string;
  sortText?: string;
}

function propertyCompletion(p: PropertySpec): Completion {
  let insertText: string;
  switch (p.type) {
    case "code":
      insertText = `${p.key}:\n\t\`\`\`${p.key === "plsqlCode" ? "plsql" : p.key === "code" ? "plsql" : "sql"}\n\t$0\n\t\`\`\``;
      break;
    case "enum":
      insertText = `${p.key}: \${1|${p.values.join(",")}|}`;
      break;
    case "boolean":
      insertText = `${p.key}: \${1|true,false|}`;
      break;
    case "names":
      insertText = `${p.key}: [$1]`;
      break;
    default:
      insertText = `${p.key}: $0`;
  }
  return { label: p.key, kind: "property", insertText, detail: p.param, documentation: propertyDoc(p) };
}

function componentCompletion(spec: ComponentSpec): Completion {
  const id = spec.id === "required" ? ` \${1:${spec.idParam?.replace(/^p_/, "") ?? "name"}}` : "";
  const body = spec.kind === "plsql" ? "\tcode:\n\t\t```plsql\n\t\t$0\n\t\t```" : "\t$0";
  return {
    label: spec.kind,
    kind: "component",
    insertText: `${spec.kind}${id} (\n${body}\n)`,
    detail: spec.procedure,
    documentation: componentDoc(spec),
  };
}

export function completions(doc: ApxDocument, schema: Schema, line: number, character: number): Completion[] {
  const ctx = contextAt(doc, line, character);
  switch (ctx.type) {
    case "code":
      return [];
    case "top":
      return schema.topLevel.map((k) => componentCompletion(schema.components.get(k)!));
    case "component": {
      const spec = schema.components.get(ctx.component.kind);
      if (!spec) return [];
      const used = new Set(ctx.component.properties.map((p) => p.key));
      const usedGroups = new Set(ctx.component.groups.map((g) => g.name));
      const out: Completion[] = [];
      for (const p of spec.properties.values()) if (!used.has(p.key)) out.push({ ...propertyCompletion(p), sortText: "1" + p.key });
      for (const g of spec.groups.values()) {
        if (usedGroups.has(g.name)) continue;
        out.push({
          label: g.name,
          kind: "group",
          insertText: `${g.name} {\n\t$0\n}`,
          detail: `group: ${[...g.properties.keys()].join(", ")}`,
          sortText: "2" + g.name,
        });
      }
      for (const k of spec.children) out.push({ ...componentCompletion(schema.components.get(k)!), sortText: "3" + k });
      return out;
    }
    case "group": {
      const g = schema.components.get(ctx.component.kind)?.groups.get(ctx.group.name);
      if (!g) return [];
      const used = new Set(ctx.group.properties.map((p) => p.key));
      return [...g.properties.values()].filter((p) => !used.has(p.key)).map(propertyCompletion);
    }
    case "value": {
      const cspec = schema.components.get(ctx.component.kind);
      const spec = ctx.group ? cspec?.groups.get(ctx.group.name)?.properties.get(ctx.key) : cspec?.properties.get(ctx.key);
      if (!spec) return [];
      if (spec.type === "enum") return spec.values.map((v) => ({ label: v, kind: "value", insertText: v, detail: spec.param }));
      if (spec.type === "boolean") return ["true", "false"].map((v) => ({ label: v, kind: "value", insertText: v }));
      if (spec.type === "names" && ctx.inArray) {
        // roles -> restRole, modules -> restModule
        const wanted = schema.topLevel.filter((k) => k.toLowerCase() === "rest" + ctx.key.replace(/s$/, "").toLowerCase());
        return doc.components
          .filter((c) => c.id !== undefined && wanted.includes(c.kind))
          .map((c) => ({
            label: "@" + c.id,
            kind: "reference",
            insertText: "@" + (/^[A-Za-z0-9_][A-Za-z0-9_.-]*$/.test(c.id!) ? c.id : JSON.stringify(c.id)),
            detail: c.kind,
          }));
      }
      return [];
    }
  }
}

// --- hover and navigation ---------------------------------------------------

function propertyDoc(p: PropertySpec): string {
  const lines = [p.description ?? ""];
  if (p.param) lines.push(`ORDS parameter: \`${p.param}\``);
  if (p.type === "enum") lines.push(`Values: ${p.values.map((v) => `\`${v}\``).join(", ")}, or a quoted ORDS value`);
  return lines.filter(Boolean).join("\n\n");
}

function componentDoc(c: ComponentSpec): string {
  const lines = [c.description];
  if (c.procedure) lines.push(`Generates \`${c.procedure}\`${c.idParam ? `; the id is \`${c.idParam}\`` : ""}.`);
  if (c.children.length) lines.push(`Contains: ${c.children.map((k) => `\`${k}\``).join(", ")}`);
  return lines.filter(Boolean).join("\n\n");
}

const inSpan = (s: Span, line: number, ch: number) => s.line === line && ch >= s.start && ch <= s.end;

export interface Hover {
  markdown: string;
  span: Span;
}

export function hover(doc: ApxDocument, schema: Schema, line: number, character: number): Hover | undefined {
  for (const c of walk(doc.components)) {
    const spec = schema.components.get(c.kind);
    if (!spec) continue;
    if (inSpan(c.kindSpan, line, character)) {
      return { markdown: `**${c.kind}**\n\n${componentDoc(spec)}`, span: c.kindSpan };
    }
    const lists: Array<[ApxProperty[], Map<string, PropertySpec> | undefined, string]> = [
      [c.properties, spec.properties, c.kind],
      ...c.groups.map((g): [ApxProperty[], Map<string, PropertySpec> | undefined, string] => [g.properties, spec.groups.get(g.name)?.properties, `${c.kind} › ${g.name}`]),
    ];
    for (const g of c.groups) {
      if (inSpan(g.nameSpan, line, character)) {
        const gspec = spec.groups.get(g.name);
        if (gspec) return { markdown: `**${g.name}** (group)\n\nProperties: ${[...gspec.properties.keys()].map((k) => `\`${k}\``).join(", ")}`, span: g.nameSpan };
      }
    }
    for (const [props, specs, where] of lists) {
      for (const p of props) {
        if (inSpan(p.keySpan, line, character)) {
          const ps = specs?.get(p.key);
          if (ps) return { markdown: `**${p.key}** · ${where}\n\n${propertyDoc(ps)}`, span: p.keySpan };
          if (spec.passThrough && CAMEL_RE.test(p.key)) {
            return { markdown: `**${p.key}**\n\nNot in the grammar; passed to ORDS as \`${toParam(p.key)}\`.`, span: p.keySpan };
          }
        }
      }
    }
  }
  return undefined;
}

/** Find the component a @reference under the cursor points to. */
export function definition(doc: ApxDocument, line: number, character: number): ApxComponent | undefined {
  const targets = referenceTargets(doc);
  for (const c of walk(doc.components)) {
    for (const p of [...c.properties, ...c.groups.flatMap((g) => g.properties)]) {
      const refs: ApxValue[] = p.value?.kind === "array" ? (p.value.items ?? []) : p.value ? [p.value] : [];
      for (const r of refs) if (r.kind === "reference" && inSpan(r.span, line, character)) return targets.get(r.text);
    }
  }
  return undefined;
}

export { walk };
