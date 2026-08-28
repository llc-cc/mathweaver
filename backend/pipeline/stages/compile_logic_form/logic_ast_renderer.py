import json


__all__ = ["render_logic_ast_local"]


def render_logic_ast_local(logic_ast_local: dict, indent_size: int = 2) -> str:
    """Render one logic_ast_local dict into an operator-style S-expression."""

    binary_operator_names = {
        "imp": "Imp",
        "iff": "Iff",
        "and": "And",
        "or": "Or",
        "eq": "Eq",
        "le": "LE",
        "sub": "Sub",
        "set_diff": "SetDiff",
    }
    multiline_binary_kinds = {"imp", "iff", "and", "or"}

    def require(node, key):
        if key not in node:
            kind = node.get("kind", "<missing-kind>")
            raise ValueError(f"logic_ast node kind={kind!r} missing required field {key!r}")
        return node[key]

    def require_list(node, key):
        value = require(node, key)
        if not isinstance(value, list):
            kind = node.get("kind", "<missing-kind>")
            raise ValueError(f"logic_ast node kind={kind!r} field {key!r} must be a list")
        return value

    def indent_block(text, level):
        prefix = " " * (indent_size * level)
        return "\n".join(prefix + line if line else line for line in text.splitlines())

    def render_atom(value):
        text = "None" if value is None else str(value)
        if text and all(not char.isspace() and char not in '()"\\' for char in text):
            return text
        return json.dumps(text, ensure_ascii=False)

    def operator_name(kind):
        if not isinstance(kind, str) or not kind:
            raise ValueError("logic_ast node missing required field 'kind'")
        if kind == kind.upper():
            return kind
        parts = [part for part in kind.split("_") if part]
        if not parts:
            raise ValueError("logic_ast node missing required field 'kind'")
        return "".join(part[:1].upper() + part[1:] for part in parts)

    def render_vars(vars_):
        if not isinstance(vars_, list):
            raise ValueError("logic_ast binder field 'vars' must be a list")
        rendered_vars = []
        for var in vars_:
            if not isinstance(var, dict):
                raise ValueError("logic_ast binder variable must be a dict")
            sym_id = require(var, "sym_id")
            sort = require(var, "sort")
            rendered_vars.append(f"({render_atom(sym_id)} {render_atom(sort)})")
        return f"({' '.join(rendered_vars)})"

    def render_value(value, level):
        if isinstance(value, dict):
            return render_node(value, level)
        if isinstance(value, list):
            rendered_items = [render_value(item, 0) for item in value]
            if not rendered_items:
                return "()"
            if all("\n" not in item for item in rendered_items):
                return f"({' '.join(rendered_items)})"
            return "(\n" + "\n".join(indent_block(item, 1) for item in rendered_items) + "\n)"
        return render_atom(value)

    def render_call(operator, operands, level, force_multiline=False):
        rendered_operator = render_atom(operator)
        rendered_operands = [render_value(operand, 0) for operand in operands]
        if not rendered_operands:
            return f"({rendered_operator})"
        if not force_multiline and all("\n" not in operand for operand in rendered_operands):
            return f"({rendered_operator} {' '.join(rendered_operands)})"
        lines = [f"({rendered_operator}"]
        lines.extend(indent_block(operand, 1) for operand in rendered_operands)
        lines[-1] = lines[-1] + ")"
        return "\n".join(lines)

    def render_binder(operator, node, level):
        vars_ = require_list(node, "vars")
        body = require(node, "body")
        rendered_body = render_node(body, 0)
        return f"({render_atom(operator)} {render_vars(vars_)}\n{indent_block(rendered_body, 1)})"

    def render_unknown_binder(operator, node, level):
        vars_ = require_list(node, "vars")
        body = require(node, "body")
        extra_operands = [value for key, value in node.items() if key not in {"kind", "vars", "body"}]
        rendered_body = render_node(body, 0)
        if not extra_operands:
            return f"({render_atom(operator)} {render_vars(vars_)}\n{indent_block(rendered_body, 1)})"
        rendered_extras = [render_value(value, 0) for value in extra_operands]
        lines = [f"({render_atom(operator)} {render_vars(vars_)}", indent_block(rendered_body, 1)]
        lines.extend(indent_block(value, 1) for value in rendered_extras)
        lines[-1] = lines[-1] + ")"
        return "\n".join(lines)

    def render_pred(node, level):
        pred_id = require(node, "pred_id")
        args = require_list(node, "args")
        return render_call(pred_id, args, level)

    def render_fn_operand(fn):
        if isinstance(fn, dict) and fn.get("kind") == "pred" and "args" not in fn:
            return require(fn, "pred_id")
        return fn

    def render_app(node, level):
        fn = require(node, "fn")
        args = require_list(node, "args")
        return render_call("App", [render_fn_operand(fn)] + args, level)

    def render_sum(node, level):
        term = require(node, "term")
        if "index" in node and "from" in node and "to" in node:
            return render_call("Sum", [node["index"], node["from"], node["to"], term], level)
        if "from" in node and "to" in node:
            return render_call("Sum", [node["from"], node["to"], term], level)
        if "index" in node:
            return render_call("Sum", [node["index"], term], level)
        if "over" in node:
            return render_call("SumOver", [node["over"], term], level)
        return render_call("Sum", [term], level)

    def render_ordered_unknown_operands(node):
        operands = []
        consumed = {"kind"}

        if "fn" in node:
            fn = require(node, "fn")
            args = require_list(node, "args")
            consumed.update({"fn", "args"})
            operands.extend([render_fn_operand(fn), *args])
        elif "args" in node:
            args = require_list(node, "args")
            consumed.add("args")
            operands.extend(args)

        if "left" in node or "right" in node:
            left = require(node, "left")
            right = require(node, "right")
            consumed.update({"left", "right"})
            operands.extend([left, right])

        if "arg" in node:
            consumed.add("arg")
            operands.append(node["arg"])

        for key, value in node.items():
            if key not in consumed:
                operands.append(value)

        return operands

    def render_unknown(node, level):
        kind = require(node, "kind")
        operator = operator_name(kind)

        if ("left" in node) != ("right" in node):
            raise ValueError(f"logic_ast node kind={kind!r} requires both 'left' and 'right'")
        if ("vars" in node) != ("body" in node):
            raise ValueError(f"logic_ast node kind={kind!r} requires both 'vars' and 'body'")
        if "fn" in node and "args" not in node:
            raise ValueError(f"logic_ast node kind={kind!r} requires 'args' when 'fn' is present")

        if "vars" in node:
            return render_unknown_binder(operator, node, level)
        return render_call(operator, render_ordered_unknown_operands(node), level)

    def render_node(node, level):
        if not isinstance(node, dict):
            raise ValueError("logic_ast node must be a dict")

        kind = require(node, "kind")
        if not isinstance(kind, str) or not kind:
            raise ValueError("logic_ast node missing required field 'kind'")

        if kind == "sym_ref":
            return render_atom(require(node, "sym_id"))
        if kind == "pred":
            return render_pred(node, level)
        if kind == "forall":
            return render_binder("Forall", node, level)
        if kind == "exists":
            return render_binder("Exists", node, level)
        if kind in binary_operator_names:
            left = require(node, "left")
            right = require(node, "right")
            return render_call(
                binary_operator_names[kind],
                [left, right],
                level,
                force_multiline=kind in multiline_binary_kinds,
            )
        if kind == "not":
            arg = require(node, "arg")
            rendered_arg = render_value(arg, 0)
            force_multiline = rendered_arg.startswith("(")
            return render_call("Not", [arg], level, force_multiline=force_multiline)
        if kind == "app":
            return render_app(node, level)
        if kind == "in":
            return render_call("In", [require(node, "element"), require(node, "set")], level)
        if kind == "abs":
            return render_call("Abs", [require(node, "arg")], level)
        if kind == "power":
            return render_call("Power", [require(node, "base"), require(node, "exponent")], level)
        if kind == "index":
            return render_call("Index", [require(node, "group"), require(node, "subgroup")], level)
        if kind == "int":
            return render_call("Int", [require(node, "value")], level)
        if kind == "mul":
            if "args" in node:
                return render_call("Mul", require_list(node, "args"), level)
            return render_call("Mul", [require(node, "left"), require(node, "right")], level)
        if kind == "sum":
            return render_sum(node, level)
        if kind == "restriction":
            function = node.get("function", node.get("fn"))
            if function is None:
                function = require(node, "function")
            return render_call("Restriction", [function, require(node, "domain")], level)
        if kind == "inner_product":
            operands = [require(node, "left"), require(node, "right")]
            if "group" in node:
                operands.append(node["group"])
            return render_call("InnerProduct", operands, level)
        if kind == "conjugate":
            return render_call("Conjugate", [require(node, "arg")], level)
        if kind == "set_comprehension":
            return render_call("SetComprehension", [require(node, "element"), require(node, "index")], level)
        if kind == "conjugates":
            operands = [require(node, "element"), require(node, "group")]
            if "count" in node:
                operands.append(node["count"])
            return render_call("Conjugates", operands, level)
        if kind == "order":
            return render_call("Order", [require(node, "arg")], level)
        if kind == "div":
            return render_call("Div", [require(node, "left"), require(node, "right")], level)
        if kind == "congruent_mod":
            return render_call("CongruentMod", [require(node, "left"), require(node, "right"), require(node, "modulus")], level)
        if kind == "congruence_class":
            return render_call("CongruenceClass", [require(node, "value"), require(node, "modulus")], level)

        return render_unknown(node, level)

    def render_error(exc):
        return (
            f"(RenderError {json.dumps(type(exc).__name__, ensure_ascii=False)} "
            f"{json.dumps(str(exc), ensure_ascii=False)})"
        )

    try:
        if not isinstance(indent_size, int) or indent_size < 0:
            raise ValueError("indent_size must be a non-negative integer")
        return render_node(logic_ast_local, 0)
    except Exception as exc:
        return render_error(exc)