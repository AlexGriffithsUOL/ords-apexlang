// Derives the component schema (kinds, nesting, groups, properties, value
// types, documentation) from the parsed EBNF, so the editor follows the
// grammar file rather than a copy of it.

import { Expr, Grammar, Rule, refsOf } from "./ebnf";

export type ValueType = "boolean" | "number" | "text" | "code" | "names" | "enum" | "value";

export interface PropertySpec {
  key: string;
  type: ValueType;
  /** Enum values; a quoted string is accepted as well. */
  values: string[];
  /** ORDS parameter, e.g. p_base_path. */
  param?: string;
  description?: string;
}

export interface GroupSpec {
  name: string;
  properties: Map<string, PropertySpec>;
}

export interface ComponentSpec {
  kind: string;
  rule: string;
  id: "required" | "optional" | "none";
  procedure?: string;
  idParam?: string;
  description: string;
  properties: Map<string, PropertySpec>;
  groups: Map<string, GroupSpec>;
  /** Kinds of components that may be nested inside this one. */
  children: string[];
  /** Whether unknown camelCase properties are passed through to ORDS. */
  passThrough: boolean;
  passThroughDoc?: string;
}

export interface Schema {
  components: Map<string, ComponentSpec>;
  topLevel: string[];
  /** Every enum value, for highlighting. */
  enumValues: Set<string>;
}

export class SchemaError extends Error {}

const LAYOUT = new Set(["indent", "ws", "required-ws", "line-end", "nl", "component-id", "blank-lines"]);
const PASS_THROUGH = "pass-through-property";

function items(expr: Expr): Expr[] {
  return expr.type === "seq" ? expr.items : [expr];
}

function alternatives(expr: Expr): Expr[] {
  return expr.type === "alt" ? expr.options : [expr];
}

function firstTerm(expr: Expr): string | undefined {
  const first = items(expr)[0];
  return first?.type === "term" ? first.value : undefined;
}

function hasTerm(expr: Expr, value: string): boolean {
  return items(expr).some((e) => e.type === "term" && e.value === value);
}

function isComponentRule(rule: Rule): boolean {
  const kind = firstTerm(rule.expr);
  return kind !== undefined && /^[A-Za-z]+$/.test(kind) && hasTerm(rule.expr, "(");
}

function isGroupRule(rule: Rule): boolean {
  const seq = items(rule.expr).filter((e) => !(e.type === "ref" && LAYOUT.has(e.name)));
  return seq[0]?.type === "term" && hasTerm(rule.expr, "{");
}

function valueType(expr: Expr): { type: ValueType; values: string[] } {
  if (expr.type === "ref") {
    switch (expr.name) {
      case "boolean":
        return { type: "boolean", values: [] };
      case "number":
        return { type: "number", values: [] };
      case "array-of-name":
        return { type: "names", values: [] };
      case "code":
      case "multiline-string":
        return { type: "code", values: [] };
      case "text":
      case "string-like-value":
      case "string":
        return { type: "text", values: [] };
      default:
        return { type: "value", values: [] };
    }
  }
  if (expr.type === "alt") {
    const values = expr.options.flatMap((o) => (o.type === "term" ? [o.value] : []));
    if (values.length) return { type: "enum", values };
  }
  return { type: "value", values: [] };
}

function parseDoc(doc: string | undefined): { param?: string; description?: string } {
  if (!doc) return {};
  const m = /^(-|p_\w+):\s*([\s\S]*)$/.exec(doc);
  if (!m) return { description: doc };
  return { param: m[1] === "-" ? undefined : m[1], description: m[2].replace(/\s+/g, " ").trim() };
}

class Builder {
  constructor(private readonly grammar: Grammar) {}

  private rule(name: string): Rule {
    const rule = this.grammar.rules.get(name);
    if (!rule) throw new SchemaError(`<${name}> is referenced but not defined`);
    return rule;
  }

  private propertiesOf(rule: Rule, into: Map<string, PropertySpec>, owner: { passThrough: boolean; passThroughDoc?: string }) {
    for (const alt of alternatives(rule.expr)) {
      if (alt.type === "ref" && alt.name === PASS_THROUGH) {
        owner.passThrough = true;
        owner.passThroughDoc = this.grammar.rules.get(PASS_THROUGH)?.doc;
        continue;
      }
      const seq = items(alt);
      const key = seq[0]?.type === "term" ? seq[0].value : undefined;
      const colon = seq.findIndex((e) => e.type === "term" && e.value === ":");
      if (!key || colon !== 1) throw new SchemaError(`<${rule.name}> line ${rule.line}: expected "key" ":" <ws> value`);
      const valueExpr = seq.slice(colon + 1).filter((e) => !(e.type === "ref" && LAYOUT.has(e.name)));
      if (valueExpr.length !== 1) throw new SchemaError(`<${rule.name}> line ${rule.line}: property '${key}' needs one value`);
      const { type, values } = valueType(valueExpr[0]);
      into.set(key, { key, type, values, ...parseDoc(alt.type === "seq" ? alt.doc : undefined) });
    }
  }

  /** Walk the rules reachable from a component body, classifying each one. */
  private collectBody(expr: Expr, spec: ComponentSpec, seen: Set<string>) {
    for (const name of refsOf(expr)) {
      if (LAYOUT.has(name) || seen.has(name) || this.grammar.primitives.has(name)) continue;
      seen.add(name);
      const rule = this.rule(name);
      if (isComponentRule(rule)) {
        spec.children.push(firstTerm(rule.expr)!);
      } else if (isGroupRule(rule)) {
        const groupName = items(rule.expr).find((e) => e.type === "term")!;
        const group: GroupSpec = { name: (groupName as { value: string }).value, properties: new Map() };
        for (const ref of refsOf(rule.expr)) {
          if (!LAYOUT.has(ref)) this.propertiesOf(this.rule(ref), group.properties, { passThrough: false });
        }
        spec.groups.set(group.name, group);
      } else if (name.endsWith("-property")) {
        this.propertiesOf(rule, spec.properties, spec);
      } else if (name === PASS_THROUGH) {
        spec.passThrough = true;
      } else {
        this.collectBody(rule.expr, spec, seen);
      }
    }
  }

  component(rule: Rule): ComponentSpec {
    const kind = firstTerm(rule.expr)!;
    const seq = items(rule.expr);
    const second = seq[1];
    const id =
      second?.type === "opt" && [...refsOf(second)].includes("component-id")
        ? "optional"
        : seq.some((e) => e.type === "ref" && e.name === "component-id")
          ? "required"
          : "none";

    // documentation: "kind\nORDS.PROC; id is p_param\ndescription"
    const lines = (rule.doc ?? "").split("\n");
    const header = /^\s*([\w.-]+);\s*id is\s*([\w-]+)/.exec(lines[1] ?? "");
    const spec: ComponentSpec = {
      kind,
      rule: rule.name,
      id,
      procedure: header && header[1] !== "-" ? header[1] : undefined,
      idParam: header && header[2] !== "-" ? header[2] : undefined,
      description: (lines[0]?.trim() === kind ? lines.slice(header ? 2 : 1) : lines).join(" ").replace(/\s+/g, " ").trim(),
      properties: new Map(),
      groups: new Map(),
      children: [],
      passThrough: false,
    };
    const body = seq.slice(seq.findIndex((e) => e.type === "term" && e.value === "("));
    this.collectBody({ type: "seq", items: body }, spec, new Set());
    return spec;
  }

  build(): Schema {
    const components = new Map<string, ComponentSpec>();
    for (const rule of this.grammar.rules.values()) {
      if (isComponentRule(rule)) {
        const spec = this.component(rule);
        if (components.has(spec.kind)) throw new SchemaError(`component kind '${spec.kind}' is defined twice`);
        components.set(spec.kind, spec);
      }
    }
    const index = this.grammar.rules.get("component");
    if (!index) throw new SchemaError("the grammar has no <component> index rule");
    const topLevel = [...refsOf(index.expr)].map((name) => firstTerm(this.rule(name).expr)!);
    const enumValues = new Set<string>();
    for (const c of components.values()) {
      for (const p of [...c.properties.values(), ...[...c.groups.values()].flatMap((g) => [...g.properties.values()])]) {
        p.values.forEach((v) => enumValues.add(v));
      }
    }
    return { components, topLevel, enumValues };
  }
}

export function buildSchema(grammar: Grammar): Schema {
  return new Builder(grammar).build();
}
