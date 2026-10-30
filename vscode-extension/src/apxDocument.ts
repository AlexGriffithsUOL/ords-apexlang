// Fault-tolerant parser for .apx files. It mirrors ords2apx/apx.py, but keeps
// positions, reports problems instead of stopping at the first one, and
// copes with half-typed lines while the user is editing.

export interface Span {
  line: number; // 0-based
  start: number;
  end: number;
}

export type ValueKind = "string" | "quoted" | "number" | "boolean" | "reference" | "array" | "code";

export interface ApxValue {
  kind: ValueKind;
  text: string; // as written (unquoted for "quoted"; contents for "code")
  span: Span;
  items?: ApxValue[]; // for arrays
  lang?: string; // for code
}

export interface ApxProperty {
  key: string;
  keySpan: Span;
  value?: ApxValue;
  /** Last line the property occupies (a fenced value spans several). */
  endLine: number;
}

export interface ApxGroup {
  name: string;
  nameSpan: Span;
  properties: ApxProperty[];
  startLine: number;
  endLine: number; // line of '}' or last line of the file if unclosed
}

export interface ApxComponent {
  kind: string;
  kindSpan: Span;
  id?: string;
  idSpan?: Span;
  properties: ApxProperty[];
  groups: ApxGroup[];
  children: ApxComponent[];
  parent?: ApxComponent;
  startLine: number;
  endLine: number; // line of ')' or last line of the file if unclosed
}

export interface Problem {
  message: string;
  span: Span;
  severity: "error" | "warning";
}

export interface ApxDocument {
  components: ApxComponent[];
  problems: Problem[];
  /** Line ranges [start, end] (inclusive) of fenced blocks, fences included. */
  fences: Array<{ start: number; end: number; lang: string }>;
  lines: string[];
}

const COMPONENT_RE = /^([A-Za-z][A-Za-z0-9]*)(?:\s+("(?:[^"\\]|\\.)*"|[A-Za-z0-9_][A-Za-z0-9_.-]*))?\s*\($/;
const GROUP_RE = /^([A-Za-z][A-Za-z0-9]*)\s*\{$/;
const PROPERTY_RE = /^([A-Za-z][A-Za-z0-9]*):(?:\s+(.*))?$/;
const NUMBER_RE = /^-?\d+(\.\d+)?$/;
const FENCE = "```";

function unquote(s: string): string | undefined {
  try {
    const v = JSON.parse(s);
    return typeof v === "string" ? v : undefined;
  } catch {
    return undefined;
  }
}

class Reader {
  private i = 0;
  private readonly doc: ApxDocument;

  constructor(text: string) {
    this.doc = { components: [], problems: [], fences: [], lines: text.replace(/\r\n/g, "\n").split("\n") };
  }

  private get lines() {
    return this.doc.lines;
  }

  private problem(message: string, span: Span, severity: Problem["severity"] = "error") {
    this.doc.problems.push({ message, span, severity });
  }

  private lineSpan(line: number): Span {
    const text = this.lines[line] ?? "";
    const start = text.length - text.trimStart().length;
    return { line, start, end: Math.max(start + 1, text.trimEnd().length) };
  }

  /** Skip blank lines and comments; return the index of the next significant line. */
  private nextSignificant(): number | undefined {
    while (this.i < this.lines.length) {
      const s = this.lines[this.i].trim();
      if (!s || s.startsWith("//")) {
        this.i++;
        continue;
      }
      if (s.startsWith("/*")) {
        const start = this.i;
        while (this.i < this.lines.length && !this.lines[this.i].includes("*/")) this.i++;
        if (this.i >= this.lines.length) {
          this.problem("unterminated /* comment", this.lineSpan(start));
          return undefined;
        }
        this.i++;
        continue;
      }
      return this.i;
    }
    return undefined;
  }

  private readFence(fenceLine: number): ApxValue {
    const raw = this.lines[fenceLine];
    const indent = raw.length - raw.trimStart().length;
    const lang = raw.trim().slice(FENCE.length).trim();
    const body: string[] = [];
    let line = fenceLine + 1;
    while (line < this.lines.length && this.lines[line].trim() !== FENCE) {
      const l = this.lines[line];
      body.push(l.slice(0, indent).trim() ? l.trimStart() : l.slice(indent));
      line++;
    }
    if (line >= this.lines.length) {
      this.problem("unterminated ``` block", this.lineSpan(fenceLine));
      line = this.lines.length - 1;
    }
    this.doc.fences.push({ start: fenceLine, end: line, lang });
    this.i = line + 1;
    return { kind: "code", text: body.join("\n"), lang, span: { line: fenceLine, start: indent, end: raw.trimEnd().length } };
  }

  private scalar(raw: string, span: Span): ApxValue {
    if (raw.startsWith('"')) {
      const text = unquote(raw);
      if (text === undefined) this.problem("malformed string literal", span);
      return { kind: "quoted", text: text ?? raw, span };
    }
    if (raw.startsWith("@")) {
      const target = raw.slice(1);
      return { kind: "reference", text: target.startsWith('"') ? (unquote(target) ?? target) : target, span };
    }
    if (raw === "true" || raw === "false") return { kind: "boolean", text: raw, span };
    if (NUMBER_RE.test(raw)) return { kind: "number", text: raw, span };
    return { kind: "string", text: raw, span };
  }

  private array(line: number, startCol: number): ApxValue {
    // gather text up to the closing bracket, possibly over several lines
    const items: ApxValue[] = [];
    let l = line;
    let col = startCol + 1;
    for (;;) {
      const text = this.lines[l];
      const re = /\s*("(?:[^"\\]|\\.)*"|@"(?:[^"\\]|\\.)*"|[^\s\]]+|\])/y;
      re.lastIndex = col;
      let m: RegExpExecArray | null;
      while ((m = re.exec(text))) {
        const tok = m[1];
        const start = m.index + m[0].length - tok.length;
        if (tok === "]") {
          this.i = l + 1;
          return { kind: "array", text: "", items, span: { line, start: startCol, end: start + 1 } };
        }
        items.push(this.scalar(tok, { line: l, start, end: start + tok.length }));
      }
      l++;
      col = 0;
      if (l >= this.lines.length || PROPERTY_RE.test(this.lines[l].trim()) || this.lines[l].trim() === ")") {
        this.problem("unterminated array: missing ]", { line, start: startCol, end: startCol + 1 });
        this.i = l;
        return { kind: "array", text: "", items, span: { line, start: startCol, end: startCol + 1 } };
      }
    }
  }

  private property(line: number, m: RegExpExecArray): ApxProperty {
    const raw = this.lines[line];
    const keyStart = raw.indexOf(m[1]);
    const prop: ApxProperty = { key: m[1], keySpan: { line, start: keyStart, end: keyStart + m[1].length }, endLine: line };
    this.i = line + 1;
    const rest = (m[2] ?? "").trimEnd();
    const valueStart = raw.length - raw.trimStart().length + m[0].length - (m[2]?.length ?? 0);
    if (!rest) {
      // value is a fenced block on the following line
      const next = this.nextSignificant();
      if (next !== undefined && this.lines[next].trim().startsWith(FENCE)) {
        prop.value = this.readFence(next);
        prop.endLine = this.i - 1;
      } else {
        this.i = line + 1;
        this.problem(`property '${prop.key}' has no value`, prop.keySpan);
      }
      return prop;
    }
    if (rest.startsWith(FENCE)) {
      prop.value = this.readFence(line);
    } else if (rest.startsWith("[")) {
      prop.value = this.array(line, valueStart);
    } else {
      prop.value = this.scalar(rest, { line, start: valueStart, end: valueStart + rest.length });
    }
    prop.endLine = this.i - 1;
    return prop;
  }

  /** Read a body up to its closing delimiter. Returns the closing line. */
  private body(close: ")" | "}", startLine: number, target: ApxComponent | ApxGroup, depth: number): number {
    const component = "children" in target ? target : undefined;
    const keys = new Set<string>();
    for (;;) {
      const n = this.nextSignificant();
      if (n === undefined) {
        this.problem(`missing closing '${close}'`, this.lineSpan(startLine));
        return this.lines.length - 1;
      }
      const s = this.lines[n].trim();
      if (s === close) {
        this.i = n + 1;
        return n;
      }
      let m: RegExpExecArray | null;
      if ((m = PROPERTY_RE.exec(s))) {
        const prop = this.property(n, m);
        if (keys.has(prop.key)) this.problem(`duplicate property '${prop.key}'`, prop.keySpan);
        keys.add(prop.key);
        target.properties.push(prop);
      } else if (component && (m = GROUP_RE.exec(s))) {
        const raw = this.lines[n];
        const start = raw.indexOf(m[1]);
        const group: ApxGroup = {
          name: m[1],
          nameSpan: { line: n, start, end: start + m[1].length },
          properties: [],
          startLine: n,
          endLine: n,
        };
        this.i = n + 1;
        group.endLine = this.body("}", n, group, depth + 1);
        component.groups.push(group);
      } else if (component && (m = COMPONENT_RE.exec(s))) {
        const child = this.component(n, m, depth + 1);
        child.parent = component;
        component.children.push(child);
      } else if ((s === ")" || s === "}") && depth > 0) {
        // closes an outer block: report and let the caller consume it
        this.problem(`expected '${close}' before '${s}'`, this.lineSpan(n));
        return n - 1;
      } else {
        this.problem(this.explain(s), this.lineSpan(n));
        this.i = n + 1;
      }
    }
  }

  private explain(s: string): string {
    if (/^[A-Za-z][A-Za-z0-9]*$/.test(s)) return `'${s}' needs a value ('${s}: …'), a body ('${s} (' / '${s} {')`;
    if (/^[A-Za-z]\w*\s+.*\($/.test(s)) return "component ids must be identifiers or \"quoted strings\"";
    if (/^[A-Za-z][A-Za-z0-9]*\s*:/.test(s)) return "expected a space after ':'";
    return `unexpected: ${s.length > 40 ? s.slice(0, 40) + "…" : s}`;
  }

  private component(line: number, m: RegExpExecArray, depth: number): ApxComponent {
    const raw = this.lines[line];
    const kindStart = raw.indexOf(m[1]);
    const c: ApxComponent = {
      kind: m[1],
      kindSpan: { line, start: kindStart, end: kindStart + m[1].length },
      properties: [],
      groups: [],
      children: [],
      startLine: line,
      endLine: line,
    };
    if (m[2] !== undefined) {
      const idStart = raw.indexOf(m[2], kindStart + m[1].length);
      c.id = m[2].startsWith('"') ? (unquote(m[2]) ?? m[2]) : m[2];
      c.idSpan = { line, start: idStart, end: idStart + m[2].length };
    }
    this.i = line + 1;
    c.endLine = this.body(")", line, c, depth);
    return c;
  }

  read(): ApxDocument {
    for (;;) {
      const n = this.nextSignificant();
      if (n === undefined) break;
      const s = this.lines[n].trim();
      const m = COMPONENT_RE.exec(s);
      if (m) {
        this.doc.components.push(this.component(n, m, 0));
      } else {
        this.problem(s === ")" || s === "}" ? `unmatched '${s}'` : "expected a component, e.g. restModule name (", this.lineSpan(n));
        this.i = n + 1;
      }
    }
    return this.doc;
  }
}

export function parseApx(text: string): ApxDocument {
  return new Reader(text).read();
}
