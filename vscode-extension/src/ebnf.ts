// Parser for the EBNF dialect used by Oracle's apexlang.ebnf and by
// grammar/ords-apexlang.ebnf:
//
//   <rule> ::= "term" <other-rule> [ optional ] { repeated } ( a | b ) "A".."Z"
//   (* comment *)
//
// A comment on its own line(s) before a rule is that rule's documentation.
// A comment at the end of an alternative, on the same line, documents that
// alternative.

export type Expr =
  | { type: "term"; value: string }
  | { type: "ref"; name: string }
  | { type: "range"; from: string; to: string }
  | { type: "seq"; items: Expr[]; doc?: string }
  | { type: "alt"; options: Expr[] }
  | { type: "opt"; expr: Expr }
  | { type: "rep"; expr: Expr };

export interface Rule {
  name: string;
  expr: Expr;
  doc?: string;
  line: number; // 1-based
}

export interface Grammar {
  rules: Map<string, Rule>;
  /** Rules referenced but never defined: character classes like <any-character>. */
  primitives: Set<string>;
}

export class EbnfError extends Error {
  constructor(message: string, readonly line: number) {
    super(`line ${line}: ${message}`);
  }
}

type TokenType = "ref" | "term" | "define" | "|" | "[" | "]" | "{" | "}" | "(" | ")" | ".." | "comment";

interface Token {
  type: TokenType;
  value: string;
  line: number;
  endLine: number;
}

function tokenize(text: string): Token[] {
  const tokens: Token[] = [];
  let i = 0;
  let line = 1;
  const push = (type: TokenType, value: string, startLine: number) =>
    tokens.push({ type, value, line: startLine, endLine: line });
  while (i < text.length) {
    const c = text[i];
    if (c === "\n") {
      line++;
      i++;
    } else if (c === " " || c === "\t" || c === "\r") {
      i++;
    } else if (text.startsWith("(*", i)) {
      const end = text.indexOf("*)", i + 2);
      if (end < 0) throw new EbnfError("unterminated comment", line);
      const body = text.slice(i + 2, end);
      const start = line;
      line += body.split("\n").length - 1;
      push("comment", body, start);
      i = end + 2;
    } else if (c === "<") {
      const end = text.indexOf(">", i);
      if (end < 0) throw new EbnfError("unterminated <rule-name>", line);
      push("ref", text.slice(i + 1, end), line);
      i = end + 1;
    } else if (c === '"') {
      let value = "";
      i++;
      while (i < text.length && text[i] !== '"') {
        if (text[i] === "\\" && i + 1 < text.length) {
          const next = text[i + 1];
          value += next === "n" ? "\n" : next === "r" ? "\r" : next === "t" ? "\t" : next;
          i += 2;
        } else {
          if (text[i] === "\n") throw new EbnfError("unterminated string", line);
          value += text[i++];
        }
      }
      if (i >= text.length) throw new EbnfError("unterminated string", line);
      i++;
      push("term", value, line);
    } else if (text.startsWith("::=", i)) {
      push("define", "::=", line);
      i += 3;
    } else if (text.startsWith("..", i)) {
      push("..", "..", line);
      i += 2;
    } else if ("|[]{}()".includes(c)) {
      push(c as TokenType, c, line);
      i++;
    } else {
      throw new EbnfError(`unexpected character '${c}'`, line);
    }
  }
  return tokens;
}

function cleanComment(body: string): string {
  // drop the common indentation of continuation lines, keep line structure
  const lines = body.split("\n").map((l) => l.replace(/\s+$/, ""));
  const rest = lines.slice(1).filter((l) => l.trim());
  const indent = Math.min(...rest.map((l) => l.length - l.trimStart().length), Infinity);
  return [lines[0].trim(), ...lines.slice(1).map((l) => l.slice(Math.min(indent, l.length)))]
    .join("\n")
    .trim();
}

class Parser {
  private pos = 0;
  constructor(private readonly tokens: Token[]) {}

  private peek(offset = 0): Token | undefined {
    return this.tokens[this.pos + offset];
  }

  private atRuleStart(): boolean {
    return this.peek()?.type === "ref" && this.peek(1)?.type === "define";
  }

  private expect(type: TokenType): Token {
    const t = this.peek();
    if (!t || t.type !== type) {
      throw new EbnfError(`expected '${type}' but found ${t ? `'${t.value}'` : "end of file"}`, t?.line ?? 0);
    }
    this.pos++;
    return t;
  }

  parse(): Grammar {
    const rules = new Map<string, Rule>();
    let doc: string | undefined;
    while (this.pos < this.tokens.length) {
      const t = this.peek()!;
      if (t.type === "comment") {
        doc = cleanComment(t.value);
        this.pos++;
        continue;
      }
      if (!this.atRuleStart()) throw new EbnfError(`expected a rule definition, found '${t.value}'`, t.line);
      const name = this.expect("ref").value;
      this.expect("define");
      if (rules.has(name)) throw new EbnfError(`rule <${name}> is defined twice`, t.line);
      const expr = this.alternation(true);
      rules.set(name, { name, expr, doc, line: t.line });
      doc = undefined;
    }
    const primitives = new Set<string>();
    for (const rule of rules.values()) {
      for (const ref of refsOf(rule.expr)) if (!rules.has(ref)) primitives.add(ref);
    }
    return { rules, primitives };
  }

  private alternation(topLevel: boolean): Expr {
    const options = [this.sequence(topLevel)];
    while (this.peek()?.type === "|") {
      this.pos++;
      options.push(this.sequence(topLevel));
    }
    return options.length === 1 ? options[0] : { type: "alt", options };
  }

  private sequence(topLevel: boolean): Expr {
    const items: Expr[] = [];
    let doc: string | undefined;
    let lastLine = this.peek()?.line ?? 0;
    for (;;) {
      const t = this.peek();
      if (!t || (topLevel && this.atRuleStart())) break;
      if (t.type === "comment") {
        // a trailing comment documents this alternative; one on a later
        // line belongs to the next rule
        if (topLevel && t.line > lastLine) break;
        doc = cleanComment(t.value);
        this.pos++;
        continue;
      }
      const item = this.primary();
      if (!item) break;
      items.push(item);
      lastLine = this.tokens[this.pos - 1].endLine;
    }
    if (items.length === 0) throw new EbnfError("empty alternative", this.peek()?.line ?? lastLine);
    return items.length === 1 && !doc ? items[0] : { type: "seq", items, doc };
  }

  private primary(): Expr | undefined {
    const t = this.peek()!;
    switch (t.type) {
      case "ref":
        this.pos++;
        return { type: "ref", name: t.value };
      case "term":
        this.pos++;
        if (this.peek()?.type === "..") {
          this.pos++;
          return { type: "range", from: t.value, to: this.expect("term").value };
        }
        return { type: "term", value: t.value };
      case "[":
      case "{":
      case "(": {
        this.pos++;
        const inner = this.alternation(false);
        const close = t.type === "[" ? "]" : t.type === "{" ? "}" : ")";
        this.expect(close);
        return t.type === "[" ? { type: "opt", expr: inner } : t.type === "{" ? { type: "rep", expr: inner } : inner;
      }
      default:
        return undefined;
    }
  }
}

export function* refsOf(expr: Expr): Generator<string> {
  switch (expr.type) {
    case "ref":
      yield expr.name;
      break;
    case "seq":
      for (const e of expr.items) yield* refsOf(e);
      break;
    case "alt":
      for (const e of expr.options) yield* refsOf(e);
      break;
    case "opt":
    case "rep":
      yield* refsOf(expr.expr);
      break;
  }
}

export function parseEbnf(text: string): Grammar {
  return new Parser(tokenize(text)).parse();
}
