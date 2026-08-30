from pipeline.common.node import normalize_node_fields, normalize_text_with_variables


def test_normalize_root_expressions():
    assert normalize_text_with_variables([], r"\sqrt{x}") == "Sqrt(2,x)"
    assert normalize_text_with_variables([], r"\sqrt[3]{x}") == "Sqrt(3,x)"
    assert normalize_text_with_variables([], "³√x") == "Sqrt(3,x)"


def test_normalize_power_expressions():
    assert normalize_text_with_variables([], r"x^n") == "Power(x,n)"
    assert normalize_text_with_variables([], r"x^{n}") == "Power(x,n)"
    assert normalize_text_with_variables([], r"(a+b)^2") == "Power((a + b),2)"


def test_normalize_fraction_expressions():
    assert normalize_text_with_variables([], r"\frac{a}{b}") == "Div(a,b)"
    assert normalize_text_with_variables([], "a / b") == "Div(a,b)"


def test_normalize_integral_expressions():
    result = normalize_text_with_variables([], r"\int_0^1 x^2 dx")
    assert result == "Integral(Power(x,2),x,0,1)"


def test_normalize_limit_expressions():
    result = normalize_text_with_variables([], r"\lim_{x\to 0} \frac{\sin x}{x}")
    assert result == r"Limit(Div(\sin x,x),x,0)"


def test_normalization_and_variable_replacement_together():
    variables = [
        {"name": "x", "normalize_type": "X_1"},
        {"name": "n", "normalize_type": "N_1"},
        {"name": "a", "normalize_type": "A_1"},
        {"name": "b", "normalize_type": "B_1"},
    ]
    result = normalize_text_with_variables(variables, r"\sqrt[n]{a/b} + x^n")
    assert result == "Sqrt(N_1,Div(A_1,B_1)) + Power(X_1,N_1)"

def test_protect_article_a_after_be_verbs():
    variables = [
        {"name": "a", "normalize_type": "A_1"},
        {"name": "b", "normalize_type": "B_1"},
    ]
    result = normalize_text_with_variables(variables, "H is a normal subgroup and a^2 = b")
    assert result == "H is a normal subgroup and Power(A_1,2) = B_1"


def test_article_a_and_free_a_can_coexist():
    variables = [{"name": "a", "normalize_type": "A_1"}]
    result = normalize_text_with_variables(variables, "there is a unique element a")
    assert result == "there is a unique element A_1"


def test_command_boundary_symbol_replacements():
    assert normalize_text_with_variables([], r"\left(x \le y\right)") == r"\left(x <= y\right)"
    assert normalize_text_with_variables([], r"\int_0^1 x\,dx") == "Integral(x,x,0,1)"
    assert normalize_text_with_variables([], r"\in A") == "in A"
    assert normalize_text_with_variables([], r"\to B") == "-> B"
    assert normalize_text_with_variables([], "x->y") == "x -> y"


def test_protect_spaced_operatorname_from_variable_replacement():
    variables = [
        {"name": "n", "normalize_type": "INTEGER_3"},
        {"name": "H", "normalize_type": "GROUP_2"},
        {"name": "G", "normalize_type": "GROUP_1"},
    ]
    result = normalize_text_with_variables(
        variables,
        r"\operatorname { I n d } _ { H } ^ { G } \theta",
    )
    assert result == r"\operatorname{Ind} _ { H } ^ { G } \theta"
    assert "I INTEGER_3 d" not in result


def test_protect_compact_operatorname_from_power_rewrite():
    variables = [
        {"name": "H", "normalize_type": "GROUP_2"},
        {"name": "G", "normalize_type": "GROUP_1"},
    ]
    result = normalize_text_with_variables(
        variables,
        r"\operatorname{Ind}_H^G \theta",
    )
    assert result == r"\operatorname{Ind}_H^G \theta"
    assert "Power(" not in result


def test_protect_bare_math_operator_expressions():
    variables = [
        {"name": "H", "normalize_type": "GROUP_2"},
        {"name": "G", "normalize_type": "GROUP_1"},
        {"name": "V", "normalize_type": "SPACE_1"},
        {"name": "W", "normalize_type": "SPACE_2"},
    ]
    result = normalize_text_with_variables(
        variables,
        r"Ind_H^G \chi = Res_H^G \chi and Hom(V,W) = Irr(G)",
    )
    assert result == r"Ind_H^G \chi = Res_H^G \chi and Hom(V,W) = Irr(G)"


def test_latex_command_name_is_not_partially_replaced():
    variables = [{"name": "n", "normalize_type": "INTEGER_3"}]
    result = normalize_text_with_variables(variables, r"\sin n + n")
    assert result == r"\sin INTEGER_3 + INTEGER_3"


def test_regular_variables_still_normalize():
    variables = [
        {"name": "n", "normalize_type": "INTEGER_3"},
        {"name": "a", "normalize_type": "INTEGER_2"},
    ]
    result = normalize_text_with_variables(variables, "n + a^2")
    assert result == "INTEGER_3 + Power(INTEGER_2,2)"


def test_variable_replacement_treats_normalize_type_as_literal():
    token = r"SUBSPACE OF $\MATHBF{R}^N$_1"

    result = normalize_text_with_variables(
        [{"name": "V", "normalize_type": token}],
        "V is fixed",
    )

    assert result == rf"{token} is fixed"


def test_normalize_node_fields_handles_latex_variable_types():
    node = {
        "content": "Subspace example.",
        "variables": [
            {"name": "V", "type": r"subspace of $\mathbf{R}^n$"},
            {"name": "y", "type": r"vector in $\mathbf{R}^n$"},
        ],
        "conditions": [{"id": "c1", "text": "V is a subspace"}],
        "conclusions": [{"id": "q1", "text": "the dual cone of V is fixed"}],
    }

    normalized = normalize_node_fields(node)

    token = r"SUBSPACE OF $\MATHBF{R}^N$_1"
    assert normalized["variables"][0]["normalize_type"] == token
    assert normalized["conditions"][0]["text_normalized"] == f"{token} is a subspace"
    assert normalized["conclusions"][0]["text_normalized"] == f"the dual cone of {token} is fixed"


def main():
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()


if __name__ == "__main__":
    main()
